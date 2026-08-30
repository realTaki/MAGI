"""BUS-owned queries over scheduled Task definitions.

The task scheduler is a Worker, so it obtains task DTOs through these
JobBoards rather than reaching into :mod:`bus.firmware.books.taskBook`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import utcnow
from ..books.taskBook import Task, TaskRow


@dataclass
class GetTaskJob(BaseJob):
    """Read one task definition for a claimed task trigger."""

    task_id: int = 0


@dataclass
class GetTaskResult(BaseJobResult):
    task: Task | None = None


class GetTaskJobRow(BaseJobRow):
    __tablename__ = "jobs_get_task"

    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetTaskJobBoard(OperateBookJobBoard[GetTaskJob, GetTaskResult, GetTaskJobRow]):
    job_cls = GetTaskJob
    result_cls = GetTaskResult
    row_cls = GetTaskJobRow

    def _execute(self, session: Session, job: GetTaskJob) -> GetTaskResult:
        row = session.get(TaskRow, job.task_id)
        return GetTaskResult(task=None if row is None else Task.from_row(row))


@dataclass
class FireTaskJob(BaseJob):
    """Record that one Task has been handed off to the agent queue."""

    task_id: int = 0


@dataclass
class FireTaskResult(BaseJobResult):
    pass


class FireTaskJobRow(BaseJobRow):
    __tablename__ = "jobs_fire_task"

    task_id: Mapped[int] = mapped_column(Integer, nullable=False)


class FireTaskJobBoard(OperateBookJobBoard[FireTaskJob, FireTaskResult, FireTaskJobRow]):
    job_cls = FireTaskJob
    result_cls = FireTaskResult
    row_cls = FireTaskJobRow

    def _execute(self, session: Session, job: FireTaskJob) -> FireTaskResult:
        task = session.get(TaskRow, job.task_id)
        if task is None:
            return FireTaskResult(
                status=JobStatus.FAILED,
                error=f"task {job.task_id} does not exist",
            )
        task.updated_at = utcnow()
        return FireTaskResult()


@dataclass
class ListTasksJob(BaseJob):
    """Read Task definitions, optionally narrowed by their enabled state."""

    enabled: bool | None = None


@dataclass
class ListTasksResult(BaseJobResult):
    tasks: list[Task] = field(default_factory=list)


class ListTasksJobRow(BaseJobRow):
    __tablename__ = "jobs_list_tasks"

    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tasks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListTasksJobBoard(OperateBookJobBoard[ListTasksJob, ListTasksResult, ListTasksJobRow]):
    job_cls = ListTasksJob
    result_cls = ListTasksResult
    row_cls = ListTasksJobRow

    def _execute(self, session: Session, job: ListTasksJob) -> ListTasksResult:
        stmt = select(TaskRow).order_by(TaskRow.id)
        if job.enabled is not None:
            stmt = stmt.where(TaskRow.enabled.is_(job.enabled))
        return ListTasksResult(tasks=[Task.from_row(row) for row in session.scalars(stmt)])
