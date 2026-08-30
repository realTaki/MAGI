"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, cast

from sqlalchemy import Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .slot import SlotType, slot
from .time import utcnow


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


class BaseJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow]:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]]
    result_cls: type[ResultT]
    row_cls: type[RowT]

    def __init__(self, factory: EngineFactory) -> None:
        self._factory = factory

    def _session(self):
        return self._factory.session()

    @slot(
        SlotType.PUBLISH,
        next_slot="claim_post_publish",
    )
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
        values["status"] = JobStatus.PREPARING.value
        with self._session() as session:
            row = type(self).row_cls(**values)
            session.add(row)
            session.commit()
            job_id = int(row.id)
        return job_id

    def pass_claim_post_publish(self, job_id: int) -> None:
        """Skip an unstaffed post-publish stage and make the Job claimable."""
        with self._session() as session:
            session.execute(
                update(type(self).row_cls)
                .where(
                    type(self).row_cls.id == job_id,
                    type(self).row_cls.status == JobStatus.PREPARING.value,
                )
                .values(status=JobStatus.PENDING.value)
            )
            session.commit()

    @slot(
        SlotType.CLAIM_POST,
        next_slot="submit_post_publish",
        pass_if_no_worker=pass_claim_post_publish,
    )
    def claim_post_publish(self) -> JobT | None:
        with self._session() as session:
            row = session.scalar(
                select(type(self).row_cls)
                .where(type(self).row_cls.status == JobStatus.PREPARING.value)
                .order_by(type(self).row_cls.id.desc())
            )
        if row is None:
            return None
        return cast(type[JobT], self.job_cls).from_row(row)

    def pass_submit_post_publish(self, job_id: int) -> None:
        """Skip an unstaffed post-publish submit stage."""
        with self._session() as session:
            session.execute(
                update(type(self).row_cls)
                .where(
                    type(self).row_cls.id == job_id,
                    type(self).row_cls.status == JobStatus.PREPARING.value,
                )
                .values(status=JobStatus.PENDING.value)
            )
            session.commit()

    @slot(SlotType.SUBMIT_POST, pass_if_no_worker=pass_submit_post_publish)
    def submit_post_publish(self, result: BaseJobResult) -> bool:
        return self._submit_post_publish(result)

    def _submit_post_publish(self, result: BaseJobResult) -> bool:
        """Persist a post-publish Hook's final decision."""
        if result.status not in {JobStatus.PENDING, JobStatus.FAILED}:
            return False
        with self._session() as session:
            row = session.get(type(self).row_cls, result.id)
            if row is None or row.status != JobStatus.PREPARING.value:
                return False
            self._write_result(row, result, status=result.status)
            session.commit()
            return True

    @slot(SlotType.CLAIM)
    def claim(self) -> JobT | None:
        return self._claim()

    def _claim(self) -> JobT | None:
        return self._pull(JobStatus.PENDING, JobStatus.CLAIMED)

    @slot(SlotType.SUBMIT_RESULT, next_slot="claim_post_result")
    def submit_result(self, result: BaseJobResult) -> bool:
        return self._submit_result(result)

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Persist the first result only; later submissions are rejected."""
        with self._session() as session:
            row = session.get(type(self).row_cls, result.id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            self._write_result(row, result, status=JobStatus.SETTLING)
            session.commit()
            return True

    def pass_claim_post_result(self, job_id: int) -> None:
        """Finish a result immediately when no post-result worker is attached."""
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None or row.status != JobStatus.SETTLING.value:
                return
            row.status = JobStatus.FAILED.value if row.error else JobStatus.COMPLETED.value
            session.commit()

    @slot(
        SlotType.CLAIM_POST,
        pass_if_no_worker=pass_claim_post_result,
    )
    def claim_post_result(self) -> JobT | None:
        with self._session() as session:
            waiting = list(
                session.scalars(
                    select(type(self).row_cls)
                    .where(type(self).row_cls.status == JobStatus.SETTLING.value)
                    .order_by(type(self).row_cls.id.desc())
                )
            )
        for row in waiting:
            with self._session() as session:
                terminal = JobStatus.FAILED if row.error else JobStatus.COMPLETED
                claimed = session.execute(
                    update(type(self).row_cls)
                    .where(
                        type(self).row_cls.id == row.id,
                        type(self).row_cls.status == JobStatus.SETTLING.value,
                    )
                    .values(status=terminal.value)
                )
                if getattr(claimed, "rowcount", 0) != 1:
                    continue
                session.commit()
                settled = session.get(type(self).row_cls, row.id)
            if settled is not None:
                return cast(type[JobT], self.job_cls).from_row(settled)
        return None

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
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None or row.status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return None
        return type(self).result_cls.from_row(row)

    def check_job_status(self, job_id: int) -> JobStatus | None:
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
