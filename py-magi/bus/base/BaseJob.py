"""BaseJobResult, BaseJob, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, cast

from sqlalchemy import Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .go import go, wait
from .time import utcnow


class JobStatus(StrEnum):
    PREPARING = "preparing"
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    MISSING = "missing"


@dataclass
class BaseJobResult(BaseRecord):
    """Outcome of a Job. Firmware subclasses add business fields."""

    status: JobStatus = JobStatus.COMPLETED
    error: str | None = None


@dataclass
class BaseJob[ResultT: BaseJobResult](BaseRecord):
    """Generic work BaseJob. Firmware later subclasses this."""

    publisher: str 


type PostPublishHook[JobT: BaseJob, ResultT: BaseJobResult] = Callable[[JobT], ResultT]
type PostResultHook[ResultT: BaseJobResult] = Callable[[ResultT], None]


class BaseJobRow(BaseRecordMixin):
    """Queue columns. Subclasses declare the business columns."""

    __abstract__ = True

    status: Mapped[str] = mapped_column(Text, nullable=False, default=JobStatus.PREPARING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)


class BaseJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow]:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]]
    result_cls: type[ResultT]
    row_cls: type[RowT]

    def __init__(self, factory: EngineFactory) -> None:
        self._factory = factory
        self._post_publish_hooks: list[PostPublishHook[JobT, ResultT]] = []
        self._post_result_hooks: list[PostResultHook[ResultT]] = []

    def _session(self):
        return self._factory.session()

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        go(self._post_publish(replace(job, id=job_id)))
        return job_id

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
            return int(row.id)

    async def _post_publish(self, job: JobT) -> JobStatus:
        gathered = await wait(self._post_publish_hooks, job)
        failed = any(item.status is JobStatus.FAILED for item in gathered)
        status = JobStatus.FAILED if failed else JobStatus.PENDING
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job.id)
            row.status = status.value
            if failed:
                row.error = "\n".join(item.error for item in gathered if item.error)
            session.commit()
        return status

    def claim(self) -> JobT | None:
        return self._claim()

    def _claim(self) -> JobT | None:
        row_cls = type(self).row_cls
        with self._session() as session:
            row = session.scalar(
                select(row_cls)
                .where(row_cls.status == JobStatus.PENDING.value)
                .order_by(row_cls.created_at, row_cls.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = JobStatus.CLAIMED.value
            session.commit()
            return cast(type[JobT], self.job_cls).from_row(row)

    async def submit_result(self, result: BaseJobResult) -> bool:
        return self._submit_result(result)

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Persist the first result only; later submissions are rejected."""
        with self._session() as session:
            row = session.get(type(self).row_cls, result.id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            self._write_result(row, result)
            session.commit()
        go(self._post_result(cast(ResultT, result)))
        return True

    async def _post_result(self, result: ResultT) -> None:
        for hook in self._post_result_hooks:
            go(asyncio.to_thread(hook, result))

    def _write_result(self, row: RowT, result: BaseJobResult) -> None:
        prepared = replace(result, created_at=row.created_at, updated_at=utcnow())
        values = prepared.to_dict()
        values.pop("id", None)
        for key, value in values.items():
            setattr(row, key, value)

    def get_result(self, job_id: int, *, timeout: float = 5.0) -> ResultT | None:
        """Wait until this Job is ``COMPLETED`` or ``FAILED``.

        Returns ``None`` if *timeout* seconds pass first. Peek with
        :meth:`check_job_status`; this is the wait.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._session() as session:
                row = session.get(type(self).row_cls, job_id)
            if row is not None and row.status in {
                JobStatus.COMPLETED.value,
                JobStatus.FAILED.value,
            }:
                return type(self).result_cls.from_row(row)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def check_job_status(self, job_id: int) -> JobStatus:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None:
            return JobStatus.MISSING
        return JobStatus(row.status)

    def purge(self) -> int:
        """Delete Jobs older than seven days."""
        cutoff = utcnow() - timedelta(days=7)
        with self._session() as session:
            result = session.execute(
                delete(type(self).row_cls).where(type(self).row_cls.created_at < cutoff)
            )
            session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

