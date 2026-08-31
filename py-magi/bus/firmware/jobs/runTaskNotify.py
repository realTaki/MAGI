"""Claimable task-trigger work for the task Worker."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, NotifyJobBoard
from ...base.engine import EngineFactory
from ..books.taskBook import TaskBook


@dataclass
class RunTaskNotify(BaseJob):
    """One request to fire a Task.

    Callers only send ``task_id``. Conversation and contact live on
    the Task row; the Worker reads them after claim. ``manual`` marks
    an operator/tool trigger versus a cron fire.
    """

    task_id: int = 0
    manual: bool = True


@dataclass
class RunTaskNotifyResult(BaseJobResult):
    """Terminal state of a trigger. Failures use ``status`` and ``error``."""


class RunTaskNotifyRow(BaseJobRow):
    __tablename__ = "jobs_run_task_notify"

    task_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RunTaskNotifyBoard(
    NotifyJobBoard[RunTaskNotify, RunTaskNotifyResult, RunTaskNotifyRow]
):
    job_cls = RunTaskNotify
    result_cls = RunTaskNotifyResult
    row_cls = RunTaskNotifyRow

    def __init__(self, factory: EngineFactory, *, book: TaskBook) -> None:
        super().__init__(factory)
        self._tasks = book

    def _publish(self, job: RunTaskNotify) -> int:
        job_id = super()._publish(job)
        self._touch_task(job.task_id)
        return job_id

    def _touch_task(self, task_id: int) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        self._tasks.update(task)
