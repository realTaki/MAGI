"""Manage a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from dataclasses import dataclass
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
class ManageBookJobResult(BaseJobResult):
    record: dict[str, Any] | None = None
    records: list[dict[str, Any]] | None = None
    deleted_id: int | None = None


@dataclass
class ManageBookJob(BaseJob):
    """``op`` selects create / read / update / delete on ``book``."""

    result_cls: ClassVar[type[BaseJobResult]] = ManageBookJobResult
    book: str = ""
    op: BookOp = BookOp.READ

    def to_record(self) -> dict[str, Any]:
        record = super().to_record()
        record["book"] = self.book
        record["op"] = self.op.value
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = super().from_record(record)
        job.book = str(record.get("book") or "")
        job.op = BookOp(record.get("op") or BookOp.READ)
        return job


class ManageBookJobBoard(BaseJobBoard):
    """Per-BaseBook board. publish runs the op; claim is not used."""

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
                job.result = ManageBookJobResult.parse(
                    {"job_id": job.id, "status": JobStatus.COMPLETED, **outcome}
                )
                self._store.replace(job.id, job.to_record())
        except BusError as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.result = ManageBookJobResult(
                job_id=job.id, status=JobStatus.FAILED, error=str(exc)
            )
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
        payload = job.payload or {}
        if job.op is BookOp.CREATE:
            try:
                record = self.book.record_cls.parse(payload)
            except TypeError as exc:
                raise InvalidJobError(f"invalid {self.book.record_cls.__name__}: {exc}") from exc
            return {"record": self.book.add(record).to_dict()}
        if job.op is BookOp.READ:
            return self._read(payload)
        if job.op is BookOp.UPDATE:
            current = self.book.get(_record_id(payload))
            if current is None:
                raise BookNotFoundError(f"book {self.book.name!r} has no id {payload.get('id')}")
            return {"record": self.book.update(current.merge(payload)).to_dict()}
        if job.op is BookOp.DELETE:
            record_id = _record_id(payload)
            if not self.book.delete(record_id):
                raise BookNotFoundError(f"book {self.book.name!r} has no id {record_id}")
            return {"deleted_id": record_id}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "id" in payload and payload["id"] not in (None, "", 0):
            record = self.book.get(_record_id(payload))
            if record is None:
                raise BookNotFoundError(f"book {self.book.name!r} has no id {payload.get('id')}")
            return {"record": record.to_dict()}
        filters = payload.get("filter")
        if filters is not None and not isinstance(filters, dict):
            raise InvalidJobError("read filter must be an object")
        return {"records": [record.to_dict() for record in self.book.list(filters)]}


def _record_id(payload: dict[str, Any]) -> int:
    value = payload.get("id")
    if value in (None, "", 0):
        raise InvalidJobError("payload.id is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidJobError("payload.id must be an integer") from exc
