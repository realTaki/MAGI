"""Open a BaseBook. BUS executes this on publish; workers never claim it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Self

from sqlalchemy import JSON, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from .BaseBook import BaseBook, BaseRecord
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, slot
from .engine import EngineFactory
from .errors import BookNotFoundError, BusError, InvalidJobError


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

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        job = super().parse(data)
        if not isinstance(job.op, BookOp):
            job.op = BookOp(job.op)
        return job


@dataclass
class OpenBookJobResult(BaseJobResult):
    record: BaseRecord | None = None
    records: list[BaseRecord] | None = None


class OpenBookJobRow(BaseJobRow):
    __abstract__ = True

    op: Mapped[str] = mapped_column(Text, nullable=False, default=BookOp.GET.value)
    filter: Mapped[dict[str, Any] | None] = mapped_column("job_filter", JSON, nullable=True)
    record: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    records: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)


class OpenBookJobBoard(BaseJobBoard):
    """Per-BaseBook board. Subclasses set book_cls and row_cls. publish runs the op; claim is not used."""

    job_cls: ClassVar[type[BaseJob]] = OpenBookJob
    result_cls: ClassVar[type[BaseJobResult]] = OpenBookJobResult
    book_cls: ClassVar[type[BaseBook]]

    def __init__(self, factory: EngineFactory) -> None:
        super().__init__(factory)
        self.book = type(self).book_cls(factory)

    @slot
    def publish(self, job: OpenBookJob, *, worker_id: str) -> int:
        job_id = super().publish(job, worker_id=worker_id)
        if not self._slot_held("post_publish"):
            self._run(job_id, job)
        return job_id

    @slot
    def submit_post_publish(self, job_id: int, result: BaseJobResult, *, worker_id: str) -> bool:
        if not super().submit_post_publish(job_id, result, worker_id=worker_id):
            return False
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None or row.status == JobStatus.FAILED.value:
            return True
        job = self.job_cls.from_row(row)
        if isinstance(job, OpenBookJob):
            if job.record is not None:
                job.record = self._parse_record(job.record)
            self._run(job_id, job)
        return True

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

    def get_result(self, job_id: int) -> BaseJobResult | None:
        self._release_idle_hooks()
        with self._session() as session:
            row = session.get(type(self).row_cls, job_id)
        if row is None or row.status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return None
        result = OpenBookJobResult(
            id=row.id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            status=JobStatus(row.status),
            error=row.error,
        )
        if row.status == JobStatus.FAILED.value:
            return result
        if row.records is not None:
            result.records = [self._parse_record(item) for item in row.records]
            return result
        if row.record is not None:
            result.record = self._parse_record(row.record)
        return result

    def list(self, *, status: JobStatus | None = None) -> list[BaseJob]:
        row_cls = type(self).row_cls
        with self._session() as session:
            stmt = select(row_cls).order_by(row_cls.created_at, row_cls.id)
            if status is not None:
                stmt = stmt.where(row_cls.status == status.value)
            rows = list(session.scalars(stmt))
        jobs = [self.job_cls.from_row(row) for row in rows]
        for job in jobs:
            if isinstance(job, OpenBookJob) and job.record is not None:
                job.record = self._parse_record(job.record)
        return jobs

    def _release_idle_hooks(self) -> None:
        if self._slot_held("post_publish"):
            return
        row_cls = type(self).row_cls
        with self._session() as session:
            waiting = list(
                session.scalars(
                    select(row_cls)
                    .where(
                        row_cls.status.in_(
                            (JobStatus.PREPARING.value, JobStatus.HOOKING.value)
                        )
                    )
                    .order_by(row_cls.created_at, row_cls.id)
                )
            )
        for row in waiting:
            job = self.job_cls.from_row(row)
            if isinstance(job, OpenBookJob):
                if job.record is not None:
                    job.record = self._parse_record(job.record)
                self._run(row.id, job)

    def _run(self, job_id: int, job: OpenBookJob) -> None:
        try:
            self._write(job_id, JobStatus.COMPLETED, self._execute(job), None)
        except BusError as exc:
            self._write(job_id, JobStatus.FAILED, {}, str(exc))

    def _execute(self, job: OpenBookJob) -> dict[str, Any]:
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
            return {}
        raise InvalidJobError(f"unknown book op {job.op!r}")

    def _input(self, job: OpenBookJob) -> BaseRecord:
        if job.record is None:
            raise InvalidJobError("record is required")
        try:
            return self.book.record_cls.parse(job.record.to_dict())
        except TypeError as exc:
            raise InvalidJobError(f"invalid {self.book.record_cls.__name__}: {exc}") from exc

    def _get(self, job: OpenBookJob) -> dict[str, Any]:
        if job.record is not None and job.record.id:
            record = self.book.get(job.record.id)
            if record is None:
                raise BookNotFoundError(
                    f"book {self.book.record_cls.__name__!r} has no id {job.record.id}"
                )
            return {"record": record.to_dict()}
        if job.filter is not None and not isinstance(job.filter, dict):
            raise InvalidJobError("get filter must be an object")
        return {"records": [item.to_dict() for item in self.book.list(**(job.filter or {}))]}

    def _saved(self, record_id: int) -> dict[str, Any]:
        record = self.book.get(record_id)
        if record is None:
            raise BookNotFoundError(f"book {self.book.record_cls.__name__!r} has no id {record_id}")
        return record.to_dict()

    def _parse_record(self, data: BaseRecord | Mapping[str, Any]) -> BaseRecord:
        if isinstance(data, BaseRecord) and not isinstance(data, dict):
            data = data.to_dict()
        try:
            return self.book.record_cls.parse(data)
        except TypeError:
            return BaseRecord.parse(data)
