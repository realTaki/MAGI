"""Claim-based JobBoard. External workers pull work; BUS does not know them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..errors import InvalidJobError, InvalidJobStateError, JobNotFoundError
from .backends import Backend, RecordStore
from .job import Job, JobStatus
from .slot import Slot, SlotSpace


def utcnow() -> datetime:
    return datetime.now(UTC)


def persist_new_job(store: RecordStore, slots: SlotSpace, job: Job) -> Job:
    """Run publish slots and persist a PENDING job. Does not execute anything."""
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
    ) -> None:
        if job_type is Job:
            raise InvalidJobError("mount a concrete Job subclass, not Job itself")
        self.job_type = job_type
        self._backend = backend
        self._slots = slots
        self._store = backend.records(f"jobs.{job_type.type_name()}")

    def publish(self, job: Job) -> Job:
        if type(job) is not self.job_type:
            raise InvalidJobError(
                f"this board accepts {self.job_type.type_name()}, not {type(job).type_name()}"
            )
        return persist_new_job(self._store, self._slots, job)

    def claim(self) -> Job | None:
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
