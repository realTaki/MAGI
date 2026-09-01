"""BaseJobResult, BaseJob, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard runs that Job in-process: publish writes the row, executes,
and stores the result. Cross-worker Jobs use HookableJobBoard instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Text, delete
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
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


class BaseJobRow(BaseRecordMixin):
    """Queue columns. Subclasses declare the business columns."""

    __abstract__ = True

    status: Mapped[str] = mapped_column(Text, nullable=False, default=JobStatus.PREPARING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)


class BaseJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow]:
    """In-process JobBoard. ``publish`` executes and writes the result.

    There is no worker claim phase and no publish/result hook. Book-operation
    Jobs use this class. Jobs that another Worker must claim use
    :class:`~bus.base.hookableJobBoard.HookableJobBoard`.
    """

    job_cls: ClassVar[type[BaseJob]]
    result_cls: type[ResultT]
    row_cls: type[RowT]

    def __init__(self, factory: EngineFactory) -> None:
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        result = self._execute(replace(job, id=job_id))
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job_id)
            self._write_result(row, result)
            session.commit()
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

    def _execute(self, job: JobT) -> ResultT:
        """Operate on the Book. Firmware boards implement this."""
        raise NotImplementedError

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
