"""BaseJob, BaseJobResult, then BaseJobBoard.

A BaseJob is something that needs to happen, is happening, or has happened.
A BaseJobResult is the outcome fields on the same record.
A BaseJobBoard is the claimable container for one work BaseJob type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Self

from .backends import Backend, RecordStore
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
class BaseJob:
    """Generic work BaseJob. Firmware later subclasses this.

    Slots attach to the concrete class, not to an instance.
    """

    publisher: str | None = None
    id: int = 0
    status: JobStatus = JobStatus.PENDING
    created_at: str | None = None
    error: str | None = None

    @classmethod
    def type_name(cls) -> str:
        return cls.__qualname__

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": type(self).type_name(),
            "status": self.status.value,
            "publisher": self.publisher,
            "created_at": self.created_at,
            "error": self.error,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = cls(publisher=record.get("publisher"))
        job.id = int(record["id"])
        job.status = JobStatus(record["status"])
        job.created_at = record.get("created_at")
        job.error = record.get("error")
        return job


@dataclass
class BaseJobResult:
    """Outcome of a Job. Firmware subclasses add business fields."""

    job_id: int = 0
    status: JobStatus = JobStatus.COMPLETED
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            data[item.name] = value.value if isinstance(value, JobStatus) else value
        return data

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        allowed = {item.name for item in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in allowed}
        if not kwargs.get("job_id") and data.get("id"):
            kwargs["job_id"] = int(data["id"])
        status = kwargs.get("status")
        if status is not None and not isinstance(status, JobStatus):
            kwargs["status"] = JobStatus(status)
        return cls(**kwargs)


def persist_new_job(store: RecordStore, slots: SlotSpace, job: BaseJob) -> BaseJob:
    """Run publish slots and persist a PENDING job. Does not execute anything."""
    from .slot import Slot

    if job.id:
        raise InvalidJobError("publish accepts only a new job (id must be 0)")
    if job.status is not JobStatus.PENDING:
        raise InvalidJobError("publish accepts only a pending job")
    job_type = type(job)
    slots.fire(job_type, Slot.PRE_PUBLISH, job)
    job.created_at = utcnow()
    job.status = JobStatus.PENDING
    job.error = None
    stored = store.insert(job.to_record())
    job.id = int(stored["id"])
    slots.fire(job_type, Slot.PUBLISH, job)
    slots.fire(job_type, Slot.POST_PUBLISH, job)
    return job


def load_job(store: RecordStore, job_type: type[BaseJob], job_id: int) -> BaseJob:
    record = store.get(job_id)
    if record is None:
        raise JobNotFoundError(f"{job_type.type_name()} {job_id} not found")
    return job_type.from_record(record)


class BaseJobBoard:
    """Running container for one work BaseJob type."""

    result_cls: ClassVar[type[BaseJobResult]] = BaseJobResult

    def __init__(
        self,
        job_type: type[BaseJob],
        backend: Backend,
        slots: SlotSpace,
        *,
        collection: str | None = None,
    ) -> None:
        if job_type is BaseJob:
            raise InvalidJobError("mount a concrete BaseJob subclass, not BaseJob itself")
        self.job_type = job_type
        self._backend = backend
        self._slots = slots
        self._store = backend.records(collection or f"jobs.{job_type.type_name()}")

    def publish(self, job: BaseJob) -> BaseJob:
        if type(job) is not self.job_type:
            raise InvalidJobError(
                f"this board accepts {self.job_type.type_name()}, not {type(job).type_name()}"
            )
        return persist_new_job(self._store, self._slots, job)

    def claim(self) -> BaseJob | None:
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

    def complete(
        self,
        job_id: int,
        result: BaseJobResult | Mapping[str, Any] | None = None,
    ) -> BaseJob:
        return self._finish(job_id, JobStatus.COMPLETED, result=result, error=None)

    def fail(self, job_id: int, error: str) -> BaseJob:
        return self._finish(job_id, JobStatus.FAILED, result=None, error=error)

    def get(self, job_id: int) -> BaseJob:
        return load_job(self._store, self.job_type, job_id)

    def result(self, job_id: int) -> BaseJobResult:
        record = self._store.get(job_id)
        if record is None:
            raise JobNotFoundError(f"{self.job_type.type_name()} {job_id} not found")
        return type(self).result_cls.parse(record)

    def list(self, *, status: JobStatus | None = None) -> list[BaseJob]:
        status_value = status.value if status is not None else None
        return [
            self.job_type.from_record(record) for record in self._store.find(status=status_value)
        ]

    def _finish(
        self,
        job_id: int,
        status: JobStatus,
        *,
        result: BaseJobResult | Mapping[str, Any] | None,
        error: str | None,
    ) -> BaseJob:
        current = load_job(self._store, self.job_type, job_id)
        if current.status is not JobStatus.CLAIMED:
            raise InvalidJobStateError(
                f"{self.job_type.type_name()} {job_id} is {current.status}, not claimed"
            )
        parsed = self._coerce_result(job_id, status, result, error)
        update = {"status": status.value, "error": error, **_result_fields(parsed)}
        updated = self._store.compare_and_set(
            job_id,
            field="status",
            expect=JobStatus.CLAIMED.value,
            update=update,
        )
        if updated is None:
            raise InvalidJobStateError(f"{self.job_type.type_name()} {job_id} is no longer claimed")
        return self.job_type.from_record(updated)

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
        parsed.job_id = job_id
        parsed.status = status
        parsed.error = error
        return parsed


_RESULT_META = frozenset({"job_id", "status", "error"})


def _result_fields(result: BaseJobResult) -> dict[str, Any]:
    return {key: value for key, value in result.to_dict().items() if key not in _RESULT_META}
