"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Table, Text, select, update
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
        cls = type(self)
        if cls.job_cls is BaseJob:
            raise InvalidJobError("set job_cls on the BaseJobBoard subclass")
        if getattr(cls, "row_cls", None) is None:
            raise InvalidJobError(f"{cls.__name__} must set row_cls")
        self._factory = factory
        self._slots = slots
        table = cls.row_cls.__table__
        if isinstance(table, Table):
            table.create(factory.engine, checkfirst=True)

    def _session(self):
        return self._factory.session()

    def publish(self, job: BaseJob) -> int:
        from .slot import Slot

        now = utcnow()
        prepared = replace(
            job,
            id=0,
            created_at=now,
            updated_at=now,
        )
        job_type = type(job)
        self._slots.fire(job_type, Slot.PRE_PUBLISH, prepared)
        with self._session() as session:
            row = type(self).row_cls(**_row_kwargs(type(self).row_cls, prepared.to_dict()))
            session.add(row)
            session.commit()
            job.id = int(row.id)
        self._slots.fire(job_type, Slot.PUBLISH, job)
        self._slots.fire(job_type, Slot.POST_PUBLISH, job)
        return job.id

    def claim(self) -> BaseJob | None:
        from .slot import Slot

        with self._session() as session:
            pending = list(
                session.scalars(
                    select(type(self).row_cls)
                    .where(type(self).row_cls.status == JobStatus.PENDING.value)
                    .order_by(type(self).row_cls.created_at, type(self).row_cls.id)
                )
            )
        for row in pending:
            job = self.job_cls.from_row(row)
            self._slots.fire(self.job_cls, Slot.PRE_CLAIM, job)
            with self._session() as session:
                changed = session.execute(
                    update(type(self).row_cls)
                    .where(
                        type(self).row_cls.id == job.id,
                        type(self).row_cls.status == JobStatus.PENDING.value,
                    )
                    .values(status=JobStatus.CLAIMED.value)
                )
                if getattr(changed, "rowcount", 0) != 1:
                    continue
                session.commit()
                claimed = session.get(type(self).row_cls, job.id)
            if claimed is None:
                continue
            job = self.job_cls.from_row(claimed)
            self._slots.fire(self.job_cls, Slot.CLAIM, job)
            self._slots.fire(self.job_cls, Slot.POST_CLAIM, job)
            return job
        return None

    def submit_result(self, job_id: int, result: BaseJobResult) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None:
                return False
            if row.status != JobStatus.CLAIMED.value:
                raise InvalidJobStateError(
                    f"{self.job_cls.__qualname__} {job_id} is {row.status}, not claimed"
                )
            prepared = replace(
                result,
                id=row.id,
                created_at=row.created_at,
                updated_at=utcnow(),
            )
            for key, value in _row_kwargs(type(self).row_cls, prepared.to_dict()).items():
                setattr(row, key, value)
            session.commit()
            return True

    def get_result(self, job_id: int) -> BaseJobResult | None:
        row = self._get_row(job_id)
        if row.status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return None
        parsed = type(self).result_cls.from_row(row)
        parsed.status = JobStatus(row.status)
        parsed.error = row.error
        return parsed

    def check_job_status(self, job_id: int) -> JobStatus | None:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None:
            return None
        return JobStatus(row.status)

    def list(self, *, status: JobStatus | None = None) -> list[BaseJob]:
        with self._session() as session:
            stmt = select(type(self).row_cls).order_by(type(self).row_cls.created_at, type(self).row_cls.id)
            if status is not None:
                stmt = stmt.where(type(self).row_cls.status == status.value)
            rows = list(session.scalars(stmt))
        return [self.job_cls.from_row(row) for row in rows]

    def _get_row(self, job_id: int) -> BaseJobRow:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.__qualname__} {job_id} not found")
            return row

    def _write(
        self, job_id: int, status: JobStatus, extra: Mapping[str, Any], error: str | None
    ) -> None:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None:
                raise JobNotFoundError(f"{self.job_cls.__qualname__} {job_id} not found")
            values = {"status": status.value, "error": error, **extra}
            for key, value in _row_kwargs(type(self).row_cls, values).items():
                setattr(row, key, value)
            session.commit()


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return dump_dt(value)
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _row_kwargs(row_cls: type[BaseJobRow], values: Mapping[str, Any]) -> dict[str, Any]:
    columns = {column.key: column for column in row_cls.__table__.columns}
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key == "id":
            continue
        column = columns.get(key)
        if column is None:
            continue
        out[key] = _json_ready(value) if isinstance(column.type, JSON) else value
    return out
