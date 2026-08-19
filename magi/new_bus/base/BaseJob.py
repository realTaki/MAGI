"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Self

from sqlalchemy import Table, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .errors import InvalidJobError, InvalidJobStateError, JobNotFoundError
from .time import dump_dt, utcnow

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

    @classmethod
    def type_name(cls) -> str:
        return cls.__qualname__

    def to_record(self) -> dict[str, Any]:
        record = self.to_dict()
        record["type"] = type(self).type_name()
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        return cls.parse(record)


@dataclass
class BaseJobResult(BaseRecord):
    """Outcome of a Job. Firmware subclasses add business fields."""

    status: JobStatus = JobStatus.COMPLETED
    error: str | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        parsed = super().parse(data)
        if not isinstance(parsed.status, JobStatus):
            parsed.status = JobStatus(parsed.status)
        return parsed


class BaseJobRow(BaseRecordMixin):
    """One JobBoard table: queue columns plus JSON for the rest."""

    __abstract__ = True

    status: Mapped[str] = mapped_column(Text, nullable=False, default=JobStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[str] = mapped_column(Text, nullable=False)


_JOB_ROWS: dict[str, type[BaseJobRow]] = {}


def job_row_type(table: str) -> type[BaseJobRow]:
    if table not in _JOB_ROWS:
        _JOB_ROWS[table] = type(table, (BaseJobRow,), {"__tablename__": table})
    return _JOB_ROWS[table]


class BaseJobBoard:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]] = BaseJob
    result_cls: ClassVar[type[BaseJobResult]] = BaseJobResult

    def __init__(self, factory: EngineFactory, slots: SlotSpace) -> None:
        if type(self).job_cls is BaseJob:
            raise InvalidJobError("set job_cls on the BaseJobBoard subclass")
        self._factory = factory
        self._slots = slots
        self._row_cls = job_row_type(self._table_name())
        table = self._row_cls.__table__
        if isinstance(table, Table):
            table.create(factory.engine, checkfirst=True)

    def _session(self):
        return self._factory.session()

    def _table_name(self) -> str:
        return f"jobs_{self.job_cls.type_name()}".replace(".", "_")

    def publish(self, job: BaseJob) -> BaseJob:
        if type(job) is not self.job_cls:
            raise InvalidJobError(
                f"this board accepts {self.job_cls.type_name()}, not {type(job).type_name()}"
            )
        from .slot import Slot

        if job.id:
            raise InvalidJobError("publish accepts only a new job (id must be 0)")
        job_type = type(job)
        self._slots.fire(job_type, Slot.PRE_PUBLISH, job)
        job.created_at = utcnow()
        record = job.to_record()
        record["status"] = JobStatus.PENDING.value
        record["error"] = None
        with self._session() as session:
            row = self._row_cls(
                status=JobStatus.PENDING.value,
                error=None,
                publisher=job.publisher,
                created_at=job.created_at,
                data=_dump(record),
            )
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
            job = self.job_cls.from_record(self._load(row))
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
            job = self.job_cls.from_record(self._load(claimed))
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
        return self.job_cls.from_record(self._row(job_id))

    def result(self, job_id: int) -> BaseJobResult:
        return type(self).result_cls.parse(self._row(job_id))

    def list(self, *, status: JobStatus | None = None) -> list[BaseJob]:
        with self._session() as session:
            stmt = select(self._row_cls).order_by(self._row_cls.created_at, self._row_cls.id)
            if status is not None:
                stmt = stmt.where(self._row_cls.status == status.value)
            rows = list(session.scalars(stmt))
        return [self.job_cls.from_record(self._load(row)) for row in rows]

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
                raise JobNotFoundError(f"{self.job_cls.type_name()} {job_id} not found")
            if row.status != JobStatus.CLAIMED.value:
                raise InvalidJobStateError(
                    f"{self.job_cls.type_name()} {job_id} is {row.status}, not claimed"
                )
            parsed = self._coerce_result(job_id, status, result, error)
            record = self._load(row)
            record.update(_result_fields(parsed))
            record["status"] = status.value
            record["error"] = error
            changed = session.execute(
                update(self._row_cls)
                .where(
                    self._row_cls.id == job_id,
                    self._row_cls.status == JobStatus.CLAIMED.value,
                )
                .values(status=status.value, error=error, data=_dump(record))
            )
            if getattr(changed, "rowcount", 0) != 1:
                raise InvalidJobStateError(
                    f"{self.job_cls.type_name()} {job_id} is no longer claimed"
                )
            session.commit()
        return self.get(job_id)

    def _row(self, job_id: int) -> dict[str, Any]:
        with self._session() as session:
            row = session.get(self._row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.type_name()} {job_id} not found")
            return self._load(row)

    def _load(self, row: BaseJobRow) -> dict[str, Any]:
        record = json.loads(row.data)
        record["id"] = row.id
        record["status"] = row.status
        record["error"] = row.error
        record["created_at"] = dump_dt(row.created_at)
        return record

    def _write(self, job_id: int, status: JobStatus, extra: Mapping[str, Any], error: str | None) -> None:
        with self._session() as session:
            row = session.get(self._row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.type_name()} {job_id} not found")
            record = self._load(row)
            record.update(extra)
            record["status"] = status.value
            record["error"] = error
            row.status = status.value
            row.error = error
            row.data = _dump(record)
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


def _dump(data: Mapping[str, Any]) -> str:
    return json.dumps(data, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
