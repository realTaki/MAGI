"""Per-Book JobBoard. One Job type, ``op`` selects create/read/update/delete.

BUS executes the op on publish. These jobs never enter the claim path.
"""

from __future__ import annotations

from typing import Any

from ..errors import BusError, InvalidJobError
from .backend import Backend
from .book import Book
from .job import BookOp, JobStatus, ManageBookJob
from .job_board import load_job, persist_new_job
from .slot import SlotSpace


class ManageBookJobBoard:
    def __init__(
        self,
        book: Book,
        backend: Backend,
        slots: SlotSpace,
        job_type: type[ManageBookJob] = ManageBookJob,
    ) -> None:
        if not issubclass(job_type, ManageBookJob):
            raise InvalidJobError("book board job_type must be a ManageBookJob")
        self.book = book
        self.job_type = job_type
        self._backend = backend
        self._slots = slots
        self._store = backend.records(f"jobs.book.{book.name}")

    def publish(self, job: ManageBookJob) -> ManageBookJob:
        if not isinstance(job, ManageBookJob):
            raise InvalidJobError("book board only accepts ManageBookJob")
        if job.book != self.book.name:
            raise InvalidJobError(f"job.book is {job.book!r}, this board is {self.book.name!r}")
        if not job.book:
            raise InvalidJobError("ManageBookJob.book is required")
        persist_new_job(self._store, self._slots, job)
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

    def get(self, job_id: str) -> ManageBookJob:
        job = load_job(self._store, self.job_type, job_id)
        assert isinstance(job, ManageBookJob)
        return job

    def list(self, *, status: JobStatus | None = None) -> list[ManageBookJob]:
        status_value = status.value if status is not None else None
        jobs: list[ManageBookJob] = []
        for record in self._store.find(status=status_value):
            jobs.append(self.job_type.from_record(record))
        return jobs

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
