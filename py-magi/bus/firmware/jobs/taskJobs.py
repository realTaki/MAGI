"""BUS-owned queries over scheduled Task definitions.

The task scheduler is a Worker, so it obtains task DTOs through these
JobBoards rather than reaching into :mod:`bus.firmware.books.taskBook`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Boolean, Integer
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
