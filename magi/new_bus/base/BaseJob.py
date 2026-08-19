"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Table, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .errors import InvalidJobError, InvalidJobStateError, JobNotFoundError
from .time import utcnow

if TYPE_CHECKING:
    from .slot import SlotSpace


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BaseJob(BaseRecord):
    """Generic work BaseJob. Firmware later subclasses this.

    Slots attach to the concrete class, not to an instance.
    """

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


class BaseJobBoard:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]] = BaseJob
    result_cls: ClassVar[type[BaseJobResult]] = BaseJobResult
    row_cls: ClassVar[type[BaseJobRow]]

    def __init__(self, factory: EngineFactory, slots: SlotSpace) -> None:
        if type(self).job_cls is BaseJob:
            raise InvalidJobError("set job_cls on the BaseJobBoard subclass")
        self._factory = factory
        self._slots = slots
        self._row_cls = self._rows()
        table = self._row_cls.__table__
        if isinstance(table, Table):
            table.create(factory.engine, checkfirst=True)

    def _rows(self) -> type[BaseJobRow]:
        row_cls = getattr(type(self), "row_cls", None)
        if row_cls is None:
            raise InvalidJobError("set row_cls on the BaseJobBoard subclass")
        return row_cls

    def _session(self):
        return self._factory.session()

    def publish(self, job: BaseJob) -> BaseJob:
        if type(job) is not self.job_cls:
            raise InvalidJobError(
                f"this board accepts {self.job_cls.__qualname__}, not {type(job).__qualname__}"
            )
        from .slot import Slot

        if job.id:
            raise InvalidJobError("publish accepts only a new job (id must be 0)")
        job_type = type(job)
        self._slots.fire(job_type, Slot.PRE_PUBLISH, job)
        job.created_at = utcnow()
        values = job.to_dict()
        values.pop("id", None)
        values["status"] = JobStatus.PENDING.value
        values["error"] = None
        with self._session() as session:
            row = self._row_cls(**_row_kwargs(self._row_cls, values))
            session.add(row)
            session.commit()
            job.id = int(row.id)
        self._slots.fire(job_type, Slot.PUBLISH, job)
        self._slots.fire(job_type, Slot.POST_PUBLISH, job)
        return job

    def claim(self) -> BaseJob | None:
        from .slot import Slot

        with self._session() as session:
            pending = list(
                session.scalars(
                    select(self._row_cls)
                    .where(self._row_cls.status == JobStatus.PENDING.value)
                    .order_by(self._row_cls.created_at, self._row_cls.id)
                )
            )
        for row in pending:
            job = self.job_cls.from_row(row)
            self._slots.fire(self.job_cls, Slot.PRE_CLAIM, job)
            with self._session() as session:
                changed = session.execute(
                    update(self._row_cls)
                    .where(
                        self._row_cls.id == job.id,
                        self._row_cls.status == JobStatus.PENDING.value,
                    )
                    .values(status=JobStatus.CLAIMED.value)
                )
                if getattr(changed, "rowcount", 0) != 1:
                    continue
                session.commit()
                claimed = session.get(self._row_cls, job.id)
            if claimed is None:
                continue
            job = self.job_cls.from_row(claimed)
            self._slots.fire(self.job_cls, Slot.CLAIM, job)
            self._slots.fire(self.job_cls, Slot.POST_CLAIM, job)
            return job
        return None

    def complete(
        self,
        job_id: int,
        result: BaseJobResult | Mapping[str, Any] | None = None,
    ) -> BaseJob:
        return self._finish(job_id, JobStatus.COMPLETED, result=result, error=None)

    def fail(self, job_id: int, error: str) -> BaseJob:
        return self._finish(job_id, JobStatus.FAILED, result=None, error=error)

    def get(self, job_id: int) -> BaseJob:
        return self.job_cls.from_row(self._get_row(job_id))

    def result(self, job_id: int) -> BaseJobResult:
        row = self._get_row(job_id)
        parsed = type(self).result_cls.from_row(row)
        parsed.status = JobStatus(row.status)
        parsed.error = row.error
        return parsed

    def list(self, *, status: JobStatus | None = None) -> list[BaseJob]:
        with self._session() as session:
            stmt = select(self._row_cls).order_by(self._row_cls.created_at, self._row_cls.id)
            if status is not None:
                stmt = stmt.where(self._row_cls.status == status.value)
            rows = list(session.scalars(stmt))
        return [self.job_cls.from_row(row) for row in rows]

    def _finish(
        self,
        job_id: int,
        status: JobStatus,
        *,
        result: BaseJobResult | Mapping[str, Any] | None,
        error: str | None,
    ) -> BaseJob:
        with self._session() as session:
            row = session.get(self._row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.__qualname__} {job_id} not found")
            if row.status != JobStatus.CLAIMED.value:
                raise InvalidJobStateError(
                    f"{self.job_cls.__qualname__} {job_id} is {row.status}, not claimed"
                )
            parsed = self._coerce_result(job_id, status, result, error)
            patch = {
                "status": status.value,
                "error": error,
                **_result_fields(parsed),
            }
            changed = session.execute(
                update(self._row_cls)
                .where(
                    self._row_cls.id == job_id,
                    self._row_cls.status == JobStatus.CLAIMED.value,
                )
                .values(**_row_kwargs(self._row_cls, patch))
            )
            if getattr(changed, "rowcount", 0) != 1:
                raise InvalidJobStateError(
                    f"{self.job_cls.__qualname__} {job_id} is no longer claimed"
                )
            session.commit()
        return self.get(job_id)

    def _get_row(self, job_id: int) -> BaseJobRow:
        with self._session() as session:
            row = session.get(self._row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.__qualname__} {job_id} not found")
            return row

    def _write(
        self, job_id: int, status: JobStatus, extra: Mapping[str, Any], error: str | None
    ) -> None:
        with self._session() as session:
            row = session.get(self._row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.__qualname__} {job_id} not found")
            values = {"status": status.value, "error": error, **extra}
            for key, value in _row_kwargs(self._row_cls, values).items():
                setattr(row, key, value)
            session.commit()

    def _coerce_result(
        self,
        job_id: int,
        status: JobStatus,
        result: BaseJobResult | Mapping[str, Any] | None,
        error: str | None,
    ) -> BaseJobResult:
        cls = type(self).result_cls
        if result is None:
            parsed: BaseJobResult = cls()
        elif isinstance(result, BaseJobResult):
            parsed = result
        else:
            parsed = cls.parse(result)
        parsed.id = job_id
        parsed.status = status
        parsed.error = error
        return parsed


_RESULT_META = frozenset({"id", "created_at", "updated_at", "status", "error"})


def _result_fields(result: BaseJobResult) -> dict[str, Any]:
    return {key: value for key, value in result.to_dict().items() if key not in _RESULT_META}


def _row_kwargs(row_cls: type[BaseJobRow], values: Mapping[str, Any]) -> dict[str, Any]:
    names = {column.key for column in row_cls.__table__.columns}
    return {key: value for key, value in values.items() if key in names}
