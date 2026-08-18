"""Manage a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from .backends import Backend
from .book import BaseBook
from .errors import BusError, InvalidJobError
from .job import BaseJob, BaseJobBoard, JobStatus
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

    def claim(self) -> BaseJob | None:
        raise InvalidJobError("ManageBookJob is executed by BUS and cannot be claimed")

    def complete(self, job_id: str, result: Any = None) -> BaseJob:
        del job_id, result
        raise InvalidJobError("ManageBookJob completes itself")

    def fail(self, job_id: str, error: str) -> BaseJob:
        del job_id, error
        raise InvalidJobError("ManageBookJob fails itself")

    def _execute(self, job: ManageBookJob) -> Any:
        payload = job.payload or {}
        if job.op is BookOp.CREATE:
            return {"record": self.book.insert(self.book.parse(payload)).to_dict()}
        if job.op is BookOp.READ:
            return self._read(payload)
        if job.op is BookOp.UPDATE:
            record_id = payload.get("id")
            if not record_id:
                raise InvalidJobError("update requires payload.id")
            current = self.book.require(str(record_id))
            return {"record": self.book.update(self.book.merge(current, payload)).to_dict()}
        if job.op is BookOp.DELETE:
            record_id = payload.get("id")
            if not record_id:
                raise InvalidJobError("delete requires payload.id")
            self.book.delete(str(record_id))
            return {"id": str(record_id)}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "id" in payload and payload["id"] not in (None, ""):
            return {"record": self.book.require(str(payload["id"])).to_dict()}
        filters = payload.get("filter")
        if filters is not None and not isinstance(filters, dict):
            raise InvalidJobError("read filter must be an object")
        return {"records": [record.to_dict() for record in self.book.query(filters)]}
