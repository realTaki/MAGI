"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from functools import wraps
from typing import Any, ClassVar, cast

from sqlalchemy import Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .errors import InvalidJobError
from .heartbeat import Heartbeat, Slot
from .time import utcnow


def slot(fn):
    """Guard a public JobBoard operation with its worker slot.

    The wrapped method declares only business arguments.  ``worker_id`` belongs
    to this concurrency boundary and is deliberately not passed inward.
    """

    @wraps(fn)
    def wrapped(self, *args, worker_id: str, **kwargs):
        slot_key = Slot(type(self).job_cls, fn.__name__)
        if not self._heartbeat.holds(worker_id, slot_key):
            raise InvalidJobError(f"slot {fn.__name__!r} is not held by {worker_id}")
        self._heartbeat.heartbeat(worker_id)
        return fn(self, *args, **kwargs)

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


class BaseJobRow(BaseRecordMixin):
    """Queue columns. Subclasses declare the business columns."""

    __abstract__ = True

    status: Mapped[str] = mapped_column(Text, nullable=False, default=JobStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)


class BaseJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow]:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]]
    result_cls: type[ResultT]
    row_cls: type[RowT]

    def __init__(self, factory: EngineFactory, heartbeat: Heartbeat) -> None:
        self._factory = factory
        self._heartbeat = heartbeat

    def _session(self):
        return self._factory.session()

    def _slot_held(self, name: str) -> bool:
        return self._heartbeat.held(Slot(type(self).job_cls, name))

    @classmethod
    def has_slot(cls, name: str) -> bool:
        return bool(getattr(getattr(cls, name, None), "_slot", False))

    def release_idle_slots(self) -> None:
        skip_publish = self._slot_held("post_publish")
        skip_result = self._slot_held("post_result")
        if skip_publish and skip_result:
            return
        row_cls = type(self).row_cls
        with self._session() as session:
            if not skip_publish:
                session.execute(
                    update(row_cls)
                    .where(row_cls.status.in_((JobStatus.PREPARING.value, JobStatus.HOOKING.value)))
                    .values(status=JobStatus.PENDING.value)
                )
            if not skip_result:
                waiting = (JobStatus.SETTLING.value, JobStatus.FINALIZING.value)
                session.execute(
                    update(row_cls)
                    .where(row_cls.status.in_(waiting), row_cls.error.is_(None))
                    .values(status=JobStatus.COMPLETED.value)
                )
                session.execute(
                    update(row_cls)
                    .where(row_cls.status.in_(waiting), row_cls.error.is_not(None))
                    .values(status=JobStatus.FAILED.value)
                )
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
            if self._slot_held("post_publish")
            else JobStatus.PENDING.value
        )
        with self._session() as session:
            row = type(self).row_cls(**values)
            session.add(row)
            session.commit()
            return int(row.id)

    @slot
    def post_publish(self) -> JobT | None:
        return self._post_publish()

    def _post_publish(self) -> JobT | None:
        return self._pull(JobStatus.PREPARING, JobStatus.HOOKING)

    @slot
    def submit_post_publish(self, job: JobT, result: BaseJobResult) -> bool:
        return self._submit_post_publish(job, result)

    def _submit_post_publish(self, job: JobT, result: BaseJobResult) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, job.id)
            if row is None or row.status != JobStatus.HOOKING.value:
                return False
            self._write_result(row, result)
            session.commit()
            return True

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
                status=JobStatus.SETTLING if self._slot_held("post_result") else None,
            )
            session.commit()
            return True

    @slot
    def post_result(self) -> JobT | None:
        return self._post_result()

    def _post_result(self) -> JobT | None:
        return self._pull(JobStatus.SETTLING, JobStatus.FINALIZING)

    @slot
    def submit_post_result(self, job_id: int, result: BaseJobResult) -> bool:
        return self._submit_post_result(job_id, result)

    def _submit_post_result(self, job_id: int, result: BaseJobResult) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None or row.status != JobStatus.FINALIZING.value:
                return False
            self._write_result(row, result)
            session.commit()
            return True

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
