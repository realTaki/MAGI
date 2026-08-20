"""Open a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Self, get_type_hints

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseBook
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, slot
from .engine import EngineFactory
from .errors import BookNotFoundError, BusError, InvalidJobError
from .time import dump_dt, load_dt


class BookOp(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class OpenBookJob(BaseJob):
    """``op`` selects create / read / update / delete on this board's book."""

    op: BookOp = BookOp.READ
    record_id: int = 0
    filter: dict[str, Any] | None = None
    values: dict[str, Any] = field(default_factory=dict)

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

    op: Mapped[str] = mapped_column(Text, nullable=False, default=BookOp.READ.value)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filter: Mapped[dict[str, Any] | None] = mapped_column("job_filter", JSON, nullable=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
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
        job = replace(
            job,
            filter=_json_ready(job.filter) if job.filter is not None else None,
            values=_json_ready(job.values),
        )
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

    def _execute(self, job: OpenBookJob) -> Any:
        if job.op is BookOp.CREATE:
            try:
                record = self.book.record_cls.parse(job.values)
            except TypeError as exc:
                raise InvalidJobError(f"invalid {self.book.record_cls.__name__}: {exc}") from exc
            return {"record": self._record(self.book.add(record))}
        if job.op is BookOp.READ:
            return self._read(job)
        if job.op is BookOp.UPDATE:
            record_id = _record_id(job)
            if not self.book.exists(record_id):
                raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
            current = self.book.get(record_id)
            if current is None:
                raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
            hints = get_type_hints(type(current))
            allowed = {item.name for item in fields(type(current))}
            updated = replace(
                current,
                **{
                    key: load_dt(hints.get(key), value)
                    for key, value in job.values.items()
                    if key in allowed
                },
            )
            if not self.book.update(updated):
                raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
            return {"record": self._record(record_id)}
        if job.op is BookOp.DELETE:
            record_id = _record_id(job)
            if not self.book.delete(record_id):
                raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
            return {"deleted_id": record_id}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _read(self, job: OpenBookJob) -> dict[str, Any]:
        if job.record_id:
            record = self.book.get(job.record_id)
            if record is None:
                raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {job.record_id}")
            return {"record": _json_ready(record.to_dict())}
        if job.filter is not None and not isinstance(job.filter, dict):
            raise InvalidJobError("read filter must be an object")
        return {
            "records": [
                _json_ready(record.to_dict()) for record in self.book.list(**(job.filter or {}))
            ]
        }

    def _record(self, record_id: int) -> dict[str, Any]:
        record = self.book.get(record_id)
        if record is None:
            raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
        return _json_ready(record.to_dict())


def _record_id(job: OpenBookJob) -> int:
    if not job.record_id:
        raise InvalidJobError("record_id is required")
    return job.record_id


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return dump_dt(value)
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value
