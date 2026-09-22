"""Semantic Firmware commands for MemoryBook."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateBookJob import OperateBookJobBoard
from ..books.memoryBook import Memory, MemoryKind

@dataclass
class CreateMemoryJob(BaseJob):
    detail: str
    topic: str = "New Memory"
    kind: MemoryKind = MemoryKind.LONG_TERM


@dataclass
class CreateMemoryResult(BaseJobResult):
    memory_id: int | None = None


class CreateMemoryJobRow(BaseJobRow):
    __tablename__ = "jobs_create_memory"

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CreateMemoryJobBoard(
    OperateBookJobBoard[CreateMemoryJob, CreateMemoryResult, CreateMemoryJobRow]
):
    job_cls = CreateMemoryJob
    result_cls = CreateMemoryResult
    row_cls = CreateMemoryJobRow

    def _execute(self, job: CreateMemoryJob) -> CreateMemoryResult:
        memory_id = self._book.add(
            Memory(topic=job.topic, detail=job.detail, kind=job.kind)
        )
        return CreateMemoryResult(memory_id=memory_id)


@dataclass
class GetMemoryJob(BaseJob):
    memory_id: int 


@dataclass
class GetMemoryResult(BaseJobResult):
    memory: Memory | None = None


class GetMemoryJobRow(BaseJobRow):
    __tablename__ = "jobs_get_memory"

    memory_id: Mapped[int] = mapped_column(Integer, nullable=False)
    memory: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetMemoryJobBoard(OperateBookJobBoard[GetMemoryJob, GetMemoryResult, GetMemoryJobRow]):
    job_cls = GetMemoryJob
    result_cls = GetMemoryResult
    row_cls = GetMemoryJobRow

    def _execute(self, job: GetMemoryJob) -> GetMemoryResult:
        return GetMemoryResult(memory=self._book.get(job.memory_id))


@dataclass
class ListMemoriesJob(BaseJob):
    kind: MemoryKind | None = MemoryKind.LONG_TERM
    include_archived: bool = False


@dataclass
class ListMemoriesResult(BaseJobResult):
    memories: list[Memory] | None = None


class ListMemoriesJobRow(BaseJobRow):
    __tablename__ = "jobs_list_memories"

    kind: Mapped[str] = mapped_column(Text, nullable=True)
    include_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    memories: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListMemoriesJobBoard(
    OperateBookJobBoard[ListMemoriesJob, ListMemoriesResult, ListMemoriesJobRow]
):
    job_cls = ListMemoriesJob
    result_cls = ListMemoriesResult
    row_cls = ListMemoriesJobRow

    def _execute(self, job: ListMemoriesJob) -> ListMemoriesResult:
        return ListMemoriesResult(
            memories=self._book.list(
                kind=job.kind.value if job.kind else None,
                archived=None if job.include_archived else False,
            )
        )


@dataclass
class UpdateMemoryJob(BaseJob):
    memory_id: int  
    topic: str | None = None
    detail: str | None = None
    kind: MemoryKind | None = None
    archived: bool | None = None


@dataclass
class UpdateMemoryResult(BaseJobResult):
    pass


class UpdateMemoryJobRow(BaseJobRow):
    __tablename__ = "jobs_update_memory"

    memory_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class UpdateMemoryJobBoard(
    OperateBookJobBoard[UpdateMemoryJob, UpdateMemoryResult, UpdateMemoryJobRow]
):
    job_cls = UpdateMemoryJob
    result_cls = UpdateMemoryResult
    row_cls = UpdateMemoryJobRow

    def _execute(self, job: UpdateMemoryJob) -> UpdateMemoryResult:
        self._book.update(
            Memory(
                id=job.memory_id,
                topic=job.topic,
                detail=job.detail,
                kind=job.kind,
                archived=job.archived,
            )
        )
        return UpdateMemoryResult()


@dataclass
class DeleteMemoryJob(BaseJob):
    memory_id: int  


@dataclass
class DeleteMemoryResult(BaseJobResult):
    pass


class DeleteMemoryJobRow(BaseJobRow):
    __tablename__ = "jobs_delete_memory"

    memory_id: Mapped[int] = mapped_column(Integer, nullable=False)


class DeleteMemoryJobBoard(
    OperateBookJobBoard[DeleteMemoryJob, DeleteMemoryResult, DeleteMemoryJobRow]
):
    job_cls = DeleteMemoryJob
    result_cls = DeleteMemoryResult
    row_cls = DeleteMemoryJobRow

    def _execute(self, job: DeleteMemoryJob) -> DeleteMemoryResult:
        self._book.delete(job.memory_id)
        return DeleteMemoryResult()
