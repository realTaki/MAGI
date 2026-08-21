"""Open a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Self, cast, get_args

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseBook, BaseRecord
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, slot
from .engine import EngineFactory
from .errors import InvalidJobError
from .time import utcnow


class BookOp(StrEnum):
    ADD = "add"
    GET = "get"
    UPDATE = "update"
    DELETE = "delete"


def _record_type(cls: type) -> type[BaseRecord]:
    for base in getattr(cls, "__orig_bases__", ()):
        args = get_args(base)
        if args:
            return args[0]
    raise TypeError(f"{cls.__name__} must specify RecordT")


@dataclass
class OpenBookJob[RecordT: BaseRecord](BaseJob):
    """``op`` selects add / get / update / delete on this board's book."""

    op: BookOp = BookOp.GET
    record: RecordT | None = None
    filter: dict[str, Any] | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        job = super().parse(data)
        if not isinstance(job.op, BookOp):
            job.op = BookOp(job.op)
        raw = data.get("record")
        if isinstance(raw, dict):
            job.record = cast(type[RecordT], _record_type(cls)).parse(raw)
        return job


@dataclass
class OpenBookJobResult[RecordT: BaseRecord](BaseJobResult):
    record: RecordT | None = None
    records: list[RecordT] | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        result = super().parse(data)
        record_cls = cast(type[RecordT], _record_type(cls))
        raw = data.get("record")
        if isinstance(raw, dict):
            result.record = record_cls.parse(raw)
        raw_records = data.get("records")
        if isinstance(raw_records, list):
            result.records = [
                record_cls.parse(item) for item in raw_records if isinstance(item, dict)
            ]
        return result


class OpenBookJobRow(BaseJobRow):
    __abstract__ = True

    op: Mapped[str] = mapped_column(Text, nullable=False, default=BookOp.GET.value)
    filter: Mapped[dict[str, Any] | None] = mapped_column("job_filter", JSON, nullable=True)
    record: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    records: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)


class OpenBookJobBoard[RecordT: BaseRecord](
    BaseJobBoard[OpenBookJob[RecordT], OpenBookJobResult[RecordT], OpenBookJobRow]
):
    """Per-BaseBook board. Subclasses set book_cls and row_cls. publish runs the op; claim is not used."""

    job_cls: type[OpenBookJob[RecordT]]
    result_cls: type[OpenBookJobResult[RecordT]]
    book_cls: type[BaseBook[RecordT]]

    def __init__(self, factory: EngineFactory) -> None:
        super().__init__(factory)
        self.book = type(self).book_cls(factory)

    @slot
    def publish(self, job: OpenBookJob[RecordT], *, worker_id: str) -> int:
        job_id = super().publish(job, worker_id=worker_id)
        if not self._slot_held("post_publish"):
            self._run(job_id, job)
        return job_id

    @slot
    def submit_post_publish(self, job: OpenBookJob[RecordT], result: BaseJobResult, *, worker_id: str) -> bool:
        if not super().submit_post_publish(job, result, worker_id=worker_id):
            return False
        payload = job.to_dict()
        payload.pop("id", None)
        payload.pop("created_at", None)
        payload.pop("updated_at", None)
        with self._session() as session:
            row = session.get(type(self).row_cls, job.id)
            if row is None:
                return True
            for key, value in payload.items():
                setattr(row, key, value)
            session.commit()
        if result.status == JobStatus.FAILED:
            return True
        self._run(job.id, job)
        return True

    def _write(self, job_id: int, result: OpenBookJobResult[RecordT]) -> None:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
            if row is None:
                return
            prepared = replace(result, created_at=row.created_at, updated_at=utcnow())
            values = prepared.to_dict()
            values.pop("id", None)
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()

    def claim(self, *, worker_id: str) -> OpenBookJob[RecordT] | None:
        del worker_id
        raise InvalidJobError("OpenBookJob is executed by BUS and cannot be claimed")

    def submit_result(self, job_id: int, result: BaseJobResult, *, worker_id: str) -> bool:
        del job_id, result, worker_id
        raise InvalidJobError("OpenBookJob completes itself")

    def get_result(self, job_id: int) -> OpenBookJobResult[RecordT] | None:
        self.release_idle_slots()
        self._run_pending(job_id)
        return super().get_result(job_id)

    def check_job_status(self, job_id: int) -> JobStatus | None:
        self.release_idle_slots()
        self._run_pending(job_id)
        return super().check_job_status(job_id)

    def _run_pending(self, job_id: int) -> None:
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None or row.status != JobStatus.PENDING.value:
            return
        self._run(job_id, self.job_cls.from_row(row))

    def _run(self, job_id: int, job: OpenBookJob[RecordT]) -> None:
        self._write(job_id, self._execute(job))

    def _fail(self, error: str) -> OpenBookJobResult[RecordT]:
        return type(self).result_cls(status=JobStatus.FAILED, error=error)

    def _execute(self, job: OpenBookJob[RecordT]) -> OpenBookJobResult[RecordT]:
        name = self.book.record_cls.__name__
        result_cls = type(self).result_cls
        if job.op is BookOp.GET:
            return self._get(job)
        if job.op is BookOp.DELETE:
            record_id = job.record.id if job.record is not None else 0
            if not record_id:
                return self._fail("record.id is required")
            if not self.book.delete(record_id):
                return self._fail(f"book {name!r} has no id {record_id}")
            return result_cls()
        if job.record is None:
            return self._fail("record is required")
        try:
            record = self.book.record_cls.parse(job.record.to_dict())
        except TypeError as exc:
            return self._fail(f"invalid {name}: {exc}")
        if job.op is BookOp.ADD:
            saved = self.book.get(self.book.add(record))
            if saved is None:
                return self._fail(f"book {name!r} has no id")
            return result_cls(record=saved)
        if job.op is BookOp.UPDATE:
            if not record.id:
                return self._fail("record.id is required")
            if not self.book.update(record):
                return self._fail(f"book {name!r} has no id {record.id}")
            saved = self.book.get(record.id)
            if saved is None:
                return self._fail(f"book {name!r} has no id {record.id}")
            return result_cls(record=saved)
        return self._fail(f"unknown book op {job.op!r}")

    def _get(self, job: OpenBookJob[RecordT]) -> OpenBookJobResult[RecordT]:
        name = self.book.record_cls.__name__
        result_cls = type(self).result_cls
        if job.record is not None and job.record.id:
            record = self.book.get(job.record.id)
            if record is None:
                return self._fail(f"book {name!r} has no id {job.record.id}")
            return result_cls(record=record)
        if job.filter is not None and not isinstance(job.filter, dict):
            return self._fail("get filter must be an object")
        return result_cls(records=self.book.list(**(job.filter or {})))
