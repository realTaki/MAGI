"""Manage a Book. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from ..errors import BusError, InvalidJobError
from .backends import Backend
from .book import Book
from .job import Job, JobStatus
from .job_board import JobBoard
from .slot import SlotSpace


class BookOp(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class ManageBookJob(Job):
    """``op`` selects create / read / update / delete on ``book``."""

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


class ManageBookJobBoard(JobBoard):
    """Per-Book board. publish runs the op; claim is not used."""

    def __init__(
        self,
        book: Book,
        backend: Backend,
        slots: SlotSpace,
        job_type: type[ManageBookJob] = ManageBookJob,
    ) -> None:
        if not issubclass(job_type, ManageBookJob):
            raise InvalidJobError("book board job_type must be a ManageBookJob")
        super().__init__(job_type, backend, slots, collection=f"jobs.book.{book.name}")
        self.book = book

    def publish(self, job: Job) -> ManageBookJob:
        if not isinstance(job, ManageBookJob):
            raise InvalidJobError("book board only accepts ManageBookJob")
        if not job.book:
            raise InvalidJobError("ManageBookJob.book is required")
        if job.book != self.book.name:
            raise InvalidJobError(f"job.book is {job.book!r}, this board is {self.book.name!r}")
        super().publish(job)
        try:
            with self._backend.transaction():
                result = self._execute(job)
                job.status = JobStatus.COMPLETED
                job.result = result
                job.error = None
                self._store.replace(job.id, job.to_record())
        except BusError as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.result = None
            self._store.replace(job.id, job.to_record())
        return job

    def claim(self) -> Job | None:
        raise InvalidJobError("ManageBookJob is executed by BUS and cannot be claimed")

    def complete(self, job_id: str, result: Any = None) -> Job:
        del job_id, result
        raise InvalidJobError("ManageBookJob completes itself")

    def fail(self, job_id: str, error: str) -> Job:
        del job_id, error
        raise InvalidJobError("ManageBookJob fails itself")

    def _execute(self, job: ManageBookJob) -> Any:
        payload = job.payload or {}
        if job.op is BookOp.CREATE:
            return {"record": self.book.insert(payload)}
        if job.op is BookOp.READ:
            return self._read(payload)
        if job.op is BookOp.UPDATE:
            record_id = payload.get("id")
            if not record_id:
                raise InvalidJobError("update requires payload.id")
            return {"record": self.book.update(str(record_id), payload)}
        if job.op is BookOp.DELETE:
            record_id = payload.get("id")
            if not record_id:
                raise InvalidJobError("delete requires payload.id")
            self.book.delete(str(record_id))
            return {"id": str(record_id)}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "id" in payload and payload["id"] not in (None, ""):
            record = self.book.require(str(payload["id"]))
            return {"record": record}
        filters = payload.get("filter")
        if filters is not None and not isinstance(filters, dict):
            raise InvalidJobError("read filter must be an object")
        return {"records": self.book.query(filters)}
