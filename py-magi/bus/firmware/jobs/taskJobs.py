"""BUS-owned Task definitions: read and upsert."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ..books.taskBook import Task


@dataclass
class GetTaskResult(BaseJobResult):
    task: Task | None = None


@dataclass
class GetTaskJob(BaseJob[GetTaskResult]):
    """Read one task definition for a claimed task trigger."""

    task_id: int 


class GetTaskJobRow(BaseJobRow):
    __tablename__ = "jobs_get_task"

    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetTaskJobBoard(OperateBookJobBoard[GetTaskJob, GetTaskResult, GetTaskJobRow]):
    job_cls = GetTaskJob
    result_cls = GetTaskResult
    row_cls = GetTaskJobRow

    def _execute(self, job: GetTaskJob) -> GetTaskResult:
        task = self._book.get(job.task_id)
        if task is None:
            return GetTaskResult(
                status=JobStatus.FAILED,
                error=f"task {job.task_id} does not exist",
            )
        return GetTaskResult(task=task)


@dataclass
class ListTasksResult(BaseJobResult):
    tasks: list[Task] = field(default_factory=list)


@dataclass
class ListTasksJob(BaseJob[ListTasksResult]):
    """Read Task definitions, optionally narrowed by their enabled state."""

    enabled: bool = True 


class ListTasksJobRow(BaseJobRow):
    __tablename__ = "jobs_list_tasks"

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tasks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListTasksJobBoard(OperateBookJobBoard[ListTasksJob, ListTasksResult, ListTasksJobRow]):
    job_cls = ListTasksJob
    result_cls = ListTasksResult
    row_cls = ListTasksJobRow

    def _execute(self, job: ListTasksJob) -> ListTasksResult:
        return ListTasksResult(tasks=self._book.list(enabled=job.enabled))


@dataclass
class SetTaskJob(BaseJob):
    """Create or replace one Task by unique ``name``."""

    name: str
    prompt: str
    cron: str
    conversation_id: int


@dataclass
class SetTaskResult(BaseJobResult):
    task_id: int | None = None


class SetTaskJobRow(BaseJobRow):
    __tablename__ = "jobs_set_task"

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cron: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SetTaskJobBoard(OperateBookJobBoard[SetTaskJob, SetTaskResult, SetTaskJobRow]):
    job_cls = SetTaskJob
    result_cls = SetTaskResult
    row_cls = SetTaskJobRow

    def _execute(self, job: SetTaskJob) -> SetTaskResult:
        existing = self._book.list(name=job.name)
        if existing:
            current = existing[0]
            self._book.update(
                Task(
                    id=current.id,
                    name=current.name,
                    prompt=job.prompt,
                    cron=job.cron,
                    conversation_id=current.conversation_id,
                    source=current.source,
                    enabled=current.enabled,
                )
            )
            return SetTaskResult(task_id=current.id)
        return SetTaskResult(
            task_id=self._book.add(
                Task(
                    name=job.name,
                    prompt=job.prompt,
                    cron=job.cron,
                    conversation_id=job.conversation_id,
                )
            )
        )
