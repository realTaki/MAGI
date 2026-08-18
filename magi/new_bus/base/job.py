"""Job and its JobBoard.

A Job is something that needs to happen, is happening, or has happened.
A JobBoard is the claimable container for one work Job type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self
from uuid import uuid4

from ..errors import InvalidJobError, InvalidJobStateError, JobNotFoundError
from .backends import Backend, RecordStore

if TYPE_CHECKING:
    from .slot import SlotSpace


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Job:
    """Generic work Job. Firmware later subclasses this.

    Slots attach to the concrete class, not to an instance.
    """

    payload: dict[str, Any] = field(default_factory=dict)
    publisher: str | None = None
    id: str = ""
    status: JobStatus = JobStatus.PENDING
    created_at: datetime | None = None
    result: Any = None
    error: str | None = None

    @classmethod
    def type_name(cls) -> str:
        return cls.__qualname__

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.id,
            "type": type(self).type_name(),
            "status": self.status.value,
            "publisher": self.publisher,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
        }
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = cls(
            payload=dict(record.get("payload") or {}),
            publisher=record.get("publisher"),
        )
        job.id = str(record["id"])
        job.status = JobStatus(record["status"])
        job.created_at = _parse_dt(record.get("created_at"))
        job.result = record.get("result")
        job.error = record.get("error")
        return job


def persist_new_job(store: RecordStore, slots: SlotSpace, job: Job) -> Job:
    """Run publish slots and persist a PENDING job. Does not execute anything."""
    from .slot import Slot

    if job.id:
        raise InvalidJobError("publish accepts only a new job (id must be empty)")
    if job.status is not JobStatus.PENDING:
        raise InvalidJobError("publish accepts only a pending job")
    job_type = type(job)
    slots.fire(job_type, Slot.PRE_PUBLISH, job)
    job.id = uuid4().hex
    job.created_at = utcnow()
    job.status = JobStatus.PENDING
    job.error = None
    store.insert(job.to_record())
    slots.fire(job_type, Slot.PUBLISH, job)
    slots.fire(job_type, Slot.POST_PUBLISH, job)
    return job


def load_job(store: RecordStore, job_type: type[Job], job_id: str) -> Job:
    record = store.get(job_id)
    if record is None:
        raise JobNotFoundError(f"{job_type.type_name()} {job_id} not found")
    return job_type.from_record(record)


class JobBoard:
    """Running container for one work Job type."""

    def __init__(
        self,
        job_type: type[Job],
        backend: Backend,
        slots: SlotSpace,
        *,
        collection: str | None = None,
    ) -> None:
        if job_type is Job:
            raise InvalidJobError("mount a concrete Job subclass, not Job itself")
        self.job_type = job_type
        self._backend = backend
        self._slots = slots
        self._store = backend.records(collection or f"jobs.{job_type.type_name()}")

    def publish(self, job: Job) -> Job:
        if type(job) is not self.job_type:
            raise InvalidJobError(
                f"this board accepts {self.job_type.type_name()}, not {type(job).type_name()}"
            )
        return persist_new_job(self._store, self._slots, job)

    def claim(self) -> Job | None:
        from .slot import Slot

        for record in self._store.find(status=JobStatus.PENDING.value):
            job = self.job_type.from_record(record)
            self._slots.fire(self.job_type, Slot.PRE_CLAIM, job)
            claimed = self._store.compare_and_set(
                job.id,
                field="status",
                expect=JobStatus.PENDING.value,
                update={"status": JobStatus.CLAIMED.value},
            )
            if claimed is None:
                continue
            job = self.job_type.from_record(claimed)
            self._slots.fire(self.job_type, Slot.CLAIM, job)
            self._slots.fire(self.job_type, Slot.POST_CLAIM, job)
            return job
        return None

    def complete(self, job_id: str, result: Any = None) -> Job:
        return self._finish(job_id, JobStatus.COMPLETED, result=result, error=None)

    def fail(self, job_id: str, error: str) -> Job:
        return self._finish(job_id, JobStatus.FAILED, result=None, error=error)

    def get(self, job_id: str) -> Job:
        return load_job(self._store, self.job_type, job_id)

    def list(self, *, status: JobStatus | None = None) -> list[Job]:
        status_value = status.value if status is not None else None
        return [
            self.job_type.from_record(record) for record in self._store.find(status=status_value)
        ]

    def _finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: Any,
        error: str | None,
    ) -> Job:
        current = load_job(self._store, self.job_type, job_id)
        if current.status is not JobStatus.CLAIMED:
            raise InvalidJobStateError(
                f"{self.job_type.type_name()} {job_id} is {current.status}, not claimed"
            )
        updated = self._store.compare_and_set(
            job_id,
            field="status",
            expect=JobStatus.CLAIMED.value,
            update={"status": status.value, "result": result, "error": error},
        )
        if updated is None:
            raise InvalidJobStateError(f"{self.job_type.type_name()} {job_id} is no longer claimed")
        return self.job_type.from_record(updated)
