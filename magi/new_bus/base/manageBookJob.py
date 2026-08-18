"""Manage a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Self

from .backends import Backend
from .BaseBook import BaseBook
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .errors import BookNotFoundError, BusError, InvalidJobError
from .slot import SlotSpace


class BookOp(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class ManageBookJob(BaseJob):
    """``op`` selects create / read / update / delete on ``book``."""

    book: str = ""
    op: BookOp = BookOp.READ
    record_id: int = 0
    filter: dict[str, Any] | None = None
    values: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = super().to_record()
        record.update(self.values)
        record["book"] = self.book
        record["op"] = self.op.value
        record["record_id"] = self.record_id
        record["filter"] = self.filter
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = super().from_record(record)
        job.book = str(record.get("book") or "")
        job.op = BookOp(record.get("op") or BookOp.READ)
        job.record_id = int(record.get("record_id") or 0)
        filt = record.get("filter")
        job.filter = filt if isinstance(filt, dict) else None
        job.values = {
            key: value for key, value in record.items() if key not in _MANAGE_JOB_KEYS
        }
        return job


@dataclass
class ManageBookJobResult(BaseJobResult):
    record: dict[str, Any] | None = None
    records: list[dict[str, Any]] | None = None
    deleted_id: int | None = None


class ManageBookJobBoard(BaseJobBoard):
    """Per-BaseBook board. publish runs the op; claim is not used."""

    result_cls: ClassVar[type[BaseJobResult]] = ManageBookJobResult

    def __init__(
        self,
        book: BaseBook,
        backend: Backend,
        slots: SlotSpace,
        job_type: type[ManageBookJob] = ManageBookJob,
    ) -> None:
        if not issubclass(job_type, ManageBookJob):
            raise InvalidJobError("book board job_type must be a ManageBookJob")
        super().__init__(job_type, backend, slots, collection=f"jobs.book.{book.name}")
        self.book = book

    def publish(self, job: BaseJob) -> ManageBookJob:
        if not isinstance(job, ManageBookJob):
            raise InvalidJobError("book board only accepts ManageBookJob")
        if not job.book:
            raise InvalidJobError("ManageBookJob.book is required")
        if job.book != self.book.name:
            raise InvalidJobError(f"job.book is {job.book!r}, this board is {self.book.name!r}")
        super().publish(job)
        try:
            with self._backend.transaction():
                outcome = self._execute(job)
                job.status = JobStatus.COMPLETED
                job.error = None
                record = job.to_record()
                record.update(outcome)
                self._store.replace(job.id, record)
        except BusError as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            self._store.replace(job.id, job.to_record())
        return job

    def claim(self) -> BaseJob | None:
        raise InvalidJobError("ManageBookJob is executed by BUS and cannot be claimed")

    def complete(self, job_id: int, result: Any = None) -> BaseJob:
        del job_id, result
        raise InvalidJobError("ManageBookJob completes itself")

    def fail(self, job_id: int, error: str) -> BaseJob:
        del job_id, error
        raise InvalidJobError("ManageBookJob fails itself")

    def _execute(self, job: ManageBookJob) -> Any:
        if job.op is BookOp.CREATE:
            try:
                record = self.book.record_cls.parse(job.values)
            except TypeError as exc:
                raise InvalidJobError(f"invalid {self.book.record_cls.__name__}: {exc}") from exc
            return {"record": self.book.add(record).to_dict()}
        if job.op is BookOp.READ:
            return self._read(job)
        if job.op is BookOp.UPDATE:
            current = self.book.get(_record_id(job))
            if current is None:
                raise BookNotFoundError(f"book {self.book.name!r} has no id {job.record_id}")
            return {"record": self.book.update(current.merge(job.values)).to_dict()}
        if job.op is BookOp.DELETE:
            record_id = _record_id(job)
            if not self.book.delete(record_id):
                raise BookNotFoundError(f"book {self.book.name!r} has no id {record_id}")
            return {"deleted_id": record_id}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _read(self, job: ManageBookJob) -> dict[str, Any]:
        if job.record_id:
            record = self.book.get(job.record_id)
            if record is None:
                raise BookNotFoundError(f"book {self.book.name!r} has no id {job.record_id}")
            return {"record": record.to_dict()}
        if job.filter is not None and not isinstance(job.filter, dict):
            raise InvalidJobError("read filter must be an object")
        return {"records": [record.to_dict() for record in self.book.list(job.filter)]}


def _record_id(job: ManageBookJob) -> int:
    if not job.record_id:
        raise InvalidJobError("record_id is required")
    return job.record_id


_MANAGE_JOB_KEYS = frozenset(
    {
        "id",
        "type",
        "status",
        "publisher",
        "created_at",
        "error",
        "book",
        "op",
        "record_id",
        "filter",
        "record",
        "records",
        "deleted_id",
        "job_id",
    }
)
