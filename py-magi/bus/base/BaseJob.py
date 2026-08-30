"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, cast

from sqlalchemy import Text, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .time import utcnow


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
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


type PostPublishHook[JobT: BaseJob, ResultT: BaseJobResult] = Callable[[JobT], ResultT]
type PostResultHook[ResultT: BaseJobResult] = Callable[[ResultT], None]


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

    def __init__(self, factory: EngineFactory, *, book: BaseBook | None = None) -> None:
        self._factory = factory
        self._book = book
        self._post_publish_hooks: list[PostPublishHook[JobT, ResultT]] = []
        self._post_result_hooks: list[PostResultHook[ResultT]] = []

    def _session(self):
        return self._factory.session()

    def _post_publish(self, job: JobT) -> None:
        for hook in self._post_publish_hooks:
            hook(job)

    def _post_result(self, result: ResultT) -> None:
        for hook in self._post_result_hooks:
            hook(result)

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
        values["status"] = JobStatus.PENDING.value
        with self._session() as session:
            row = type(self).row_cls(**values)
            session.add(row)
            session.commit()
            job_id = int(row.id)
        return job_id

    def claim(self) -> JobT | None:
        return self._claim()

    def _claim(self) -> JobT | None:
        return self._pull(JobStatus.PENDING, JobStatus.CLAIMED)

    def submit_result(self, result: BaseJobResult) -> bool:
        return self._submit_result(result)

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Persist the first result only; later submissions are rejected."""
        terminal = result.status
        if terminal not in {JobStatus.COMPLETED, JobStatus.FAILED}:
            terminal = JobStatus.FAILED if result.error else JobStatus.COMPLETED
        with self._session() as session:
            row = session.get(type(self).row_cls, result.id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            self._write_result(row, result, status=terminal)
            session.commit()
            return True

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

    def purge(self) -> int:
        """Delete Jobs older than seven days."""
        cutoff = utcnow() - timedelta(days=7)
        with self._session() as session:
            result = session.execute(
                delete(type(self).row_cls).where(type(self).row_cls.created_at < cutoff)
            )
            session.commit()
        return int(getattr(result, "rowcount", 0) or 0)
