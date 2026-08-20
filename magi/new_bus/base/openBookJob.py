"""Open a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Self

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseBook, BaseRecord
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, slot
from .engine import EngineFactory
from .errors import BookNotFoundError, BusError, InvalidJobError
from .time import dump_dt


class BookOp(StrEnum):
    ADD = "add"
    GET = "get"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class OpenBookJob(BaseJob):
    """``op`` selects add / get / update / delete on this board's book."""

    op: BookOp = BookOp.GET
    record: BaseRecord | None = None
    filter: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        if data.get("record") is not None:
            data["record"] = _json_ready(data["record"])
        if data.get("filter") is not None:
            data["filter"] = _json_ready(data["filter"])
        return data

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        job = super().parse(data)
        if not isinstance(job.op, BookOp):
            job.op = BookOp(job.op)
        return job


@dataclass
class OpenBookJobResult(BaseJobResult):
    record: dict[str, Any] | None = None
    records: list[dict[str, Any]] | None = None
    deleted_id: int | None = None


class OpenBookJobRow(BaseJobRow):
    __abstract__ = True

    op: Mapped[str] = mapped_column(Text, nullable=False, default=BookOp.GET.value)
    filter: Mapped[dict[str, Any] | None] = mapped_column("job_filter", JSON, nullable=True)
    record: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    records: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    deleted_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OpenBookJobBoard(BaseJobBoard):
    """Per-BaseBook board. Subclasses set book_cls and row_cls. publish runs the op; claim is not used."""

    job_cls: ClassVar[type[BaseJob]] = OpenBookJob
    result_cls: ClassVar[type[BaseJobResult]] = OpenBookJobResult
    book_cls: ClassVar[type[BaseBook]]

    def __init__(self, factory: EngineFactory) -> None:
        super().__init__(factory)
        self.book = type(self).book_cls(factory)

    @slot
    def publish(self, job: BaseJob, *, worker_id: str) -> int:
        if not isinstance(job, OpenBookJob):
            raise InvalidJobError("book board only accepts OpenBookJob")
        job_id = super().publish(job, worker_id=worker_id)
        try:
            outcome = self._execute(job)
            self._write(job_id, JobStatus.COMPLETED, outcome, None)
        except BusError as exc:
            self._write(job_id, JobStatus.FAILED, {}, str(exc))
        return job_id

    def _write(
        self, job_id: int, status: JobStatus, extra: Mapping[str, Any], error: str | None
    ) -> None:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None:
                return
            values = {"status": status.value, "error": error, **extra}
            values.pop("id", None)
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()

    def claim(self, *, worker_id: str) -> BaseJob | None:
        del worker_id
        raise InvalidJobError("OpenBookJob is executed by BUS and cannot be claimed")

    def submit_result(self, job_id: int, result: BaseJobResult, *, worker_id: str) -> bool:
        del job_id, result, worker_id
        raise InvalidJobError("OpenBookJob completes itself")

    def list(self, *, status: JobStatus | None = None) -> list[BaseJob]:
        jobs = super().list(status=status)
        for job in jobs:
            if isinstance(job, OpenBookJob):
                job.record = self._stored_record(job.record)
        return jobs

    def _execute(self, job: OpenBookJob) -> Any:
        if job.op is BookOp.ADD:
            return {"record": self._saved(self.book.add(self._input(job)))}
        if job.op is BookOp.GET:
            return self._get(job)
        if job.op is BookOp.UPDATE:
            record = self._input(job)
            if not record.id:
                raise InvalidJobError("record.id is required")
            if not self.book.update(record):
                raise BookNotFoundError(
                    f"book {self.book.record_cls.__name__!r} has no id {record.id}"
                )
            return {"record": self._saved(record.id)}
        if job.op is BookOp.DELETE:
            record_id = job.record.id if job.record is not None else 0
            if not record_id:
                raise InvalidJobError("record.id is required")
            if not self.book.delete(record_id):
                raise BookNotFoundError(
                    f"book {self.book.record_cls.__name__!r} has no id {record_id}"
                )
            return {"deleted_id": record_id}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _input(self, job: OpenBookJob) -> BaseRecord:
        if job.record is None:
            raise InvalidJobError("record is required")
        try:
            return self.book.record_cls.parse(job.record.to_dict())
        except TypeError as exc:
            raise InvalidJobError(f"invalid {self.book.record_cls.__name__}: {exc}") from exc

    def _stored_record(self, value: BaseRecord | dict[str, Any] | None) -> BaseRecord | None:
        if value is None:
            return None
        data = value.to_dict() if isinstance(value, BaseRecord) else value
        try:
            return self.book.record_cls.parse(data)
        except TypeError:
            return BaseRecord.parse(data)

    def _get(self, job: OpenBookJob) -> dict[str, Any]:
        if job.record is not None and job.record.id:
            record = self.book.get(job.record.id)
            if record is None:
                raise BookNotFoundError(
                    f"book {self.book.record_cls.__name__!r} has no id {job.record.id}"
                )
            return {"record": _json_ready(record.to_dict())}
        if job.filter is not None and not isinstance(job.filter, dict):
            raise InvalidJobError("get filter must be an object")
        return {
            "records": [
                _json_ready(record.to_dict()) for record in self.book.list(**(job.filter or {}))
            ]
        }

    def _saved(self, record_id: int) -> dict[str, Any]:
        record = self.book.get(record_id)
        if record is None:
            raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
        return _json_ready(record.to_dict())


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return dump_dt(value)
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value
