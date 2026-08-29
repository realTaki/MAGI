"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import wraps
from typing import Any, ClassVar, cast

from sqlalchemy import Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .heartbeat import Heartbeat, Slot
from .time import utcnow

_slot_worker_id: ContextVar[str | None] = ContextVar("slot_worker_id", default=None)


def slot(fn):
    """Guard a public JobBoard operation with its worker slot.

    The wrapped method declares only business arguments.  ``worker_id`` belongs
    to this concurrency boundary and is deliberately not passed inward.
    """

    @wraps(fn)
    def wrapped(self, *args, worker_id: str, **kwargs):
        slot_key = Slot(type(self).job_cls, fn.__name__)
        if not self._heartbeat.holds(worker_id, slot_key):
            return None
        self._heartbeat.heartbeat(worker_id)
        token = _slot_worker_id.set(worker_id)
        try:
            return fn(self, *args, **kwargs)
        finally:
            _slot_worker_id.reset(token)

    cast(Any, wrapped)._slot = True
    return wrapped


class JobStatus(StrEnum):
    PREPARING = "preparing"
    HOOKING = "hooking"
    PENDING = "pending"
    CLAIMED = "claimed"
    EXECUTING = "executing"
    SETTLING = "settling"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BaseJob(BaseRecord):
    """Generic work BaseJob. Firmware later subclasses this."""

    publisher: str | None = None


@dataclass
class BaseJobResult(BaseRecord):
    """Outcome of a Job. Firmware subclasses add business fields."""

    status: JobStatus = JobStatus.COMPLETED
    error: str | None = None


def error_message(error: Exception) -> str:
    """Return the durable, user-forwardable text for an execution failure."""
    return str(error).strip() or type(error).__name__


class BaseJobRow(BaseRecordMixin):
    """Queue columns. Subclasses declare the business columns."""

    __abstract__ = True

    status: Mapped[str] = mapped_column(Text, nullable=False, default=JobStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)


@dataclass
class _VoteRound:
    """One in-memory all-members submission round for one Job gate."""

    expected: set[str]
    votes: dict[str, BaseJobResult]


class BaseJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow]:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]]
    result_cls: type[ResultT]
    row_cls: type[RowT]

    def __init__(self, factory: EngineFactory, heartbeat: Heartbeat) -> None:
        self._factory = factory
        self._heartbeat = heartbeat
        self._vote_rounds: dict[tuple[str, int], _VoteRound] = {}
        self._vote_lock = threading.RLock()

    def _session(self):
        return self._factory.session()

    def _slot_held(self, name: str) -> bool:
        return self._heartbeat.held(Slot(type(self).job_cls, name))

    @classmethod
    def has_slot(cls, name: str) -> bool:
        return bool(getattr(getattr(cls, name, None), "_slot", False))

    def release_idle_slots(self) -> None:
        with self._vote_lock:
            self._settle_ready_vote_rounds()
            skip_publish = self._slot_held("claim_post_publish")
            skip_result = self._slot_held("claim_post_result")
            row_cls = type(self).row_cls
            with self._session() as session:
                # Rows written by the previous one-claimer post-gate protocol
                # remain usable after the operation names changed.
                session.execute(
                    update(row_cls)
                    .where(row_cls.status == JobStatus.HOOKING.value)
                    .values(status=JobStatus.PREPARING.value)
                )
                session.execute(
                    update(row_cls)
                    .where(row_cls.status == JobStatus.FINALIZING.value)
                    .values(status=JobStatus.SETTLING.value)
                )
                if not skip_publish:
                    session.execute(
                        update(row_cls)
                        .where(row_cls.status == JobStatus.PREPARING.value)
                        .values(status=JobStatus.PENDING.value)
                    )
                    self._discard_vote_rounds("post_publish")
                if not skip_result:
                    session.execute(
                        update(row_cls)
                        .where(row_cls.status == JobStatus.SETTLING.value, row_cls.error.is_(None))
                        .values(status=JobStatus.COMPLETED.value)
                    )
                    session.execute(
                        update(row_cls)
                        .where(row_cls.status == JobStatus.SETTLING.value, row_cls.error.is_not(None))
                        .values(status=JobStatus.FAILED.value)
                    )
                    self._discard_vote_rounds("post_result")
                session.commit()

    def _pull(self, src: JobStatus, dst: JobStatus) -> JobT | None:
        with self._session() as session:
            waiting = list(
                session.scalars(
                    select(type(self).row_cls)
                    .where(type(self).row_cls.status == src.value)
                    .order_by(type(self).row_cls.created_at, type(self).row_cls.id)
                )
            )
        for row in waiting:
            with self._session() as session:
                changed = session.execute(
                    update(type(self).row_cls)
                    .where(
                        type(self).row_cls.id == row.id,
                        type(self).row_cls.status == src.value,
                    )
                    .values(status=dst.value)
                )
                if getattr(changed, "rowcount", 0) != 1:
                    continue
                session.commit()
                pulled = session.get(type(self).row_cls, row.id)
            if pulled is None:
                continue
            return cast(type[JobT], self.job_cls).from_row(pulled)
        return None

    @slot
    def publish(self, job: JobT) -> int:
        return self._publish(job)

    def _publish(self, job: JobT) -> int:
        now = utcnow()
        prepared = replace(
            job,
            created_at=now,
            updated_at=now,
        )
        values = prepared.to_dict()
        values.pop("id", None)
        values["status"] = (
            JobStatus.PREPARING.value
            if self._slot_held("claim_post_publish")
            else JobStatus.PENDING.value
        )
        with self._session() as session:
            row = type(self).row_cls(**values)
            session.add(row)
            session.commit()
            return int(row.id)

    @slot
    def claim_post_publish(self) -> JobT | None:
        return self._claim_gate(JobStatus.PREPARING)

    def _claim_gate(self, status: JobStatus) -> JobT | None:
        with self._session() as session:
            row = session.scalar(
                select(type(self).row_cls)
                .where(type(self).row_cls.status == status.value)
                .order_by(type(self).row_cls.created_at, type(self).row_cls.id)
            )
        if row is None:
            return None
        return cast(type[JobT], self.job_cls).from_row(row)

    @slot
    def submit_post_publish(self, job: JobT, result: BaseJobResult) -> bool:
        return self._submit_post_publish(job, result)

    def _submit_post_publish(self, job: JobT, result: BaseJobResult) -> bool:
        return self._submit_gate("post_publish", int(job.id), result)

    @slot
    def claim(self) -> JobT | None:
        return self._claim()

    def _claim(self) -> JobT | None:
        self.release_idle_slots()
        return self._pull(JobStatus.PENDING, JobStatus.CLAIMED)

    @slot
    def submit_result(self, result: BaseJobResult) -> bool:
        return self._submit_result(result)

    def _submit_result(self, result: BaseJobResult) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, result.id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            self._write_result(
                row,
                result,
                status=JobStatus.SETTLING if self._slot_held("claim_post_result") else None,
            )
            session.commit()
            return True

    @slot
    def claim_post_result(self) -> JobT | None:
        return self._claim_gate(JobStatus.SETTLING)

    @slot
    def submit_post_result(self, job_id: int, result: BaseJobResult) -> bool:
        return self._submit_post_result(job_id, result)

    def _submit_post_result(self, job_id: int, result: BaseJobResult) -> bool:
        return self._submit_gate("post_result", job_id, result)

    def _submit_gate(self, stage: str, job_id: int, result: BaseJobResult) -> bool:
        worker_id = _slot_worker_id.get()
        if worker_id is None:
            return False
        with self._vote_lock:
            expected_status = (
                JobStatus.PREPARING if stage == "post_publish" else JobStatus.SETTLING
            )
            with self._session() as session:
                row = session.get(type(self).row_cls, job_id)
                if row is None or row.status != expected_status.value:
                    return False
            key = (stage, job_id)
            round_ = self._vote_rounds.get(key)
            if round_ is None:
                round_ = _VoteRound(
                    expected=self._heartbeat.members(Slot(type(self).job_cls, f"submit_{stage}")),
                    votes={},
                )
                self._vote_rounds[key] = round_
            if worker_id not in round_.expected or worker_id in round_.votes:
                return False
            round_.votes[worker_id] = result
            self._settle_vote_round(stage, job_id, round_)
            return True

    def _settle_ready_vote_rounds(self) -> None:
        for (stage, job_id), round_ in tuple(self._vote_rounds.items()):
            self._settle_vote_round(stage, job_id, round_)

    def _settle_vote_round(self, stage: str, job_id: int, round_: _VoteRound) -> None:
        slot = Slot(type(self).job_cls, f"submit_{stage}")
        round_.expected.intersection_update(self._heartbeat.members(slot))
        if not round_.expected or not round_.expected <= set(round_.votes):
            return
        failed = [vote for vote in round_.votes.values() if vote.status is JobStatus.FAILED]
        result = failed[0] if failed else next(iter(round_.votes.values()))
        if failed:
            errors = [vote.error for vote in failed if vote.error]
            result = replace(result, status=JobStatus.FAILED, error="\n".join(errors) or None)
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            expected_status = JobStatus.PREPARING if stage == "post_publish" else JobStatus.SETTLING
            if row is None or row.status != expected_status.value:
                self._vote_rounds.pop((stage, job_id), None)
                return
            next_status = (
                JobStatus.FAILED
                if failed
                else (JobStatus.PENDING if stage == "post_publish" else JobStatus.COMPLETED)
            )
            self._write_result(row, result, status=next_status)
            session.commit()
        self._vote_rounds.pop((stage, job_id), None)

    def _discard_vote_rounds(self, stage: str) -> None:
        for key in tuple(self._vote_rounds):
            if key[0] == stage:
                self._vote_rounds.pop(key, None)

    def _write_result(
        self, row: RowT, result: BaseJobResult, *, status: JobStatus | None = None
    ) -> None:
        prepared = replace(result, created_at=row.created_at, updated_at=utcnow())
        values = prepared.to_dict()
        values.pop("id", None)
        for key, value in values.items():
            setattr(row, key, value)
        if status is not None:
            row.status = status.value

    def get_result(self, job_id: int) -> ResultT | None:
        self.release_idle_slots()
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None or row.status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return None
        return type(self).result_cls.from_row(row)

    def check_job_status(self, job_id: int) -> JobStatus | None:
        self.release_idle_slots()
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None:
            return None
        return JobStatus(row.status)

    def list(self, *, status: JobStatus | None = None) -> list[JobT]:
        with self._session() as session:
            stmt = select(type(self).row_cls).order_by(
                type(self).row_cls.created_at, type(self).row_cls.id
            )
            if status is not None:
                stmt = stmt.where(type(self).row_cls.status == status.value)
            rows = list(session.scalars(stmt))
        return [cast(type[JobT], self.job_cls).from_row(row) for row in rows]
