"""Manage a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from typing import Any, ClassVar, Self, get_type_hints

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseBook
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from .engine import EngineFactory
from .errors import BookNotFoundError, BusError, InvalidJobError
from .slot import SlotSpace
from .time import load_dt


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

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        job = super().parse(data)
        if not isinstance(job.op, BookOp):
            job.op = BookOp(job.op)
        return job


@dataclass
class ManageBookJobResult(BaseJobResult):
    record: dict[str, Any] | None = None
    records: list[dict[str, Any]] | None = None
    deleted_id: int | None = None


class ManageBookJobRow(BaseJobRow):
    __abstract__ = True

    book: Mapped[str] = mapped_column(Text, nullable=False, default="")
    op: Mapped[str] = mapped_column(Text, nullable=False, default=BookOp.READ.value)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filter: Mapped[dict[str, Any] | None] = mapped_column("job_filter", JSON, nullable=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    record: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    records: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    deleted_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


_MANAGE_ROWS: dict[str, type[ManageBookJobRow]] = {}


def _manage_row(book: str) -> type[ManageBookJobRow]:
    table = f"jobs_book_{book}"
    if table not in _MANAGE_ROWS:
        _MANAGE_ROWS[table] = type(table, (ManageBookJobRow,), {"__tablename__": table})
    return _MANAGE_ROWS[table]


class ManageBookJobBoard(BaseJobBoard):
    """Per-BaseBook board. publish runs the op; claim is not used."""

    job_cls: ClassVar[type[BaseJob]] = ManageBookJob
    result_cls: ClassVar[type[BaseJobResult]] = ManageBookJobResult

    def __init__(self, book: BaseBook, factory: EngineFactory, slots: SlotSpace) -> None:
        self.book = book
        super().__init__(factory, slots)

    def _rows(self) -> type[BaseJobRow]:
        return _manage_row(self.book.record_cls.__name__)

    def publish(self, job: BaseJob) -> ManageBookJob:
        if not isinstance(job, ManageBookJob):
            raise InvalidJobError("book board only accepts ManageBookJob")
        if not job.book:
            raise InvalidJobError("ManageBookJob.book is required")
        if job.book != self.book.record_cls.__name__:
            raise InvalidJobError(
                f"job.book is {job.book!r}, this board is {self.book.record_cls.__name__!r}"
            )
        super().publish(job)
        try:
            outcome = self._execute(job)
            self._write(job.id, JobStatus.COMPLETED, outcome, None)
        except BusError as exc:
            self._write(job.id, JobStatus.FAILED, {}, str(exc))
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

    def _read(self, job: ManageBookJob) -> dict[str, Any]:
        if job.record_id:
            record = self.book.get(job.record_id)
            if record is None:
                raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {job.record_id}")
            return {"record": record.to_dict()}
        if job.filter is not None and not isinstance(job.filter, dict):
            raise InvalidJobError("read filter must be an object")
        return {"records": [record.to_dict() for record in self.book.list(**(job.filter or {}))]}

    def _record(self, record_id: int) -> dict[str, Any]:
        record = self.book.get(record_id)
        if record is None:
            raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
        return record.to_dict()


def _record_id(job: ManageBookJob) -> int:
    if not job.record_id:
        raise InvalidJobError("record_id is required")
    return job.record_id
