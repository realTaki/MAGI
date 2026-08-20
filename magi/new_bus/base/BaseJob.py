"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from functools import wraps
from typing import ClassVar

from sqlalchemy import Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseRecord, BaseRecordMixin
from .engine import EngineFactory
from .errors import InvalidJobError, InvalidJobStateError
from .time import utcnow

LEASE = timedelta(seconds=1)


def slot(fn):
    """Mark a JobBoard method as a slot. Caller must hold it via attach()."""

    @wraps(fn)
    def wrapped(self, *args, worker_id: str, **kwargs):
        now = utcnow()
        holder = self._held.get(fn.__name__)
        if holder is None or holder[0] != worker_id or holder[1] <= now:
            raise InvalidJobError(f"slot {fn.__name__!r} is not held by {worker_id}")
        self._held[fn.__name__] = (worker_id, now + LEASE)
        return fn(self, *args, worker_id=worker_id, **kwargs)

    setattr(wrapped, "_slot", True)
    return wrapped


class JobStatus(StrEnum):
    PREPARING = "preparing"
    PENDING = "pending"
    HOOKING = "hooking"
    CLAIMED = "claimed"
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


class BaseJobBoard:
    """Running container for one work BaseJob type."""

    job_cls: ClassVar[type[BaseJob]] = BaseJob
    result_cls: ClassVar[type[BaseJobResult]] = BaseJobResult
    row_cls: ClassVar[type[BaseJobRow]]

    def __init__(self, factory: EngineFactory) -> None:
        self._factory = factory
        self._held: dict[str, tuple[str, datetime] | None] = {}
        for name in dir(type(self)):
            value = getattr(type(self), name, None)
            if value is not None and getattr(value, "_slot", False):
                self._held[name] = None

    def _session(self):
        return self._factory.session()

    def attach(self, worker_id: str, slots: Sequence[str]) -> None:
        now = utcnow()
        until = now + LEASE
        for name in slots:
            if name not in self._held:
                raise InvalidJobError(f"no slot {name!r} on {type(self).__name__}")
            holder = self._held[name]
            if holder is not None and holder[1] > now and holder[0] != worker_id:
                raise InvalidJobError(f"slot {name!r} is occupied by {holder[0]}")
        for name in slots:
            self._held[name] = (worker_id, until)

    def heartbeat(self, worker_id: str) -> None:
        now = utcnow()
        until = now + LEASE
        for name, holder in list(self._held.items()):
            if holder is not None and holder[0] == worker_id and holder[1] > now:
                self._held[name] = (worker_id, until)

    def _slot_held(self, name: str) -> bool:
        holder = self._held.get(name)
        return holder is not None and holder[1] > utcnow()

    def _release_idle_hooks(self) -> None:
        skip_publish = self._slot_held("post_publish")
        skip_result = self._slot_held("post_result")
        if skip_publish and skip_result:
            return
        row_cls = type(self).row_cls
        with self._session() as session:
            if not skip_publish:
                session.execute(
                    update(row_cls)
                    .where(
                        row_cls.status.in_(
                            (JobStatus.PREPARING.value, JobStatus.HOOKING.value)
                        )
                    )
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

    def _pull(self, src: JobStatus, dst: JobStatus) -> BaseJob | None:
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
            return self.job_cls.from_row(pulled)
        return None

    @slot
    def publish(self, job: BaseJob, *, worker_id: str) -> int:
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
    def post_publish(self, *, worker_id: str) -> BaseJob | None:
        return self._pull(JobStatus.PREPARING, JobStatus.HOOKING)

    @slot
    def submit_post_publish(self, job_id: int, result: BaseJobResult, *, worker_id: str) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None:
                return False
            if row.status != JobStatus.HOOKING.value:
                raise InvalidJobStateError(
                    f"{self.job_cls.__qualname__} {job_id} is {row.status}, not hooking"
                )
            prepared = replace(
                result,
                created_at=row.created_at,
                updated_at=utcnow(),
            )
            values = prepared.to_dict()
            values.pop("id", None)
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()
            return True

    @slot
    def claim(self, *, worker_id: str) -> BaseJob | None:
        self._release_idle_hooks()
        return self._pull(JobStatus.PENDING, JobStatus.CLAIMED)

    @slot
    def submit_result(self, job_id: int, result: BaseJobResult, *, worker_id: str) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            prepared = replace(
                result,
                created_at=row.created_at,
                updated_at=utcnow(),
            )
            values = prepared.to_dict()
            values.pop("id", None)
            for key, value in values.items():
                setattr(row, key, value)
            if self._slot_held("post_result"):
                row.status = JobStatus.SETTLING.value
            session.commit()
            return True

    @slot
    def post_result(self, *, worker_id: str) -> BaseJob | None:
        return self._pull(JobStatus.SETTLING, JobStatus.FINALIZING)

    @slot
    def submit_post_result(self, job_id: int, result: BaseJobResult, *, worker_id: str) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None or row.status != JobStatus.FINALIZING.value:
                return False
            prepared = replace(
                result,
                created_at=row.created_at,
                updated_at=utcnow(),
            )
            values = prepared.to_dict()
            values.pop("id", None)
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()
            return True

    def get_result(self, job_id: int) -> BaseJobResult | None:
        self._release_idle_hooks()
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None or row.status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return None
        parsed = type(self).result_cls.from_row(row)
        parsed.status = JobStatus(row.status)
        parsed.error = row.error
        return parsed

    def check_job_status(self, job_id: int) -> JobStatus | None:
        self._release_idle_hooks()
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
