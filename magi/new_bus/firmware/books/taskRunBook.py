"""TaskRunBook — append-only execution attempts for scheduled tasks."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime, utcnow
from .taskBook import TaskRunStatus


@dataclass(kw_only=True)
class TaskRun(BaseRecord):
    """One execution attempt for a :class:`~.taskBook.Task`."""

    task_id: int
    manual: bool = False
    started_at: BaseTime = field(default_factory=utcnow)
    finished_at: BaseTime | None = None
    latency_ms: int | None = None
    status: TaskRunStatus = TaskRunStatus.RUNNING
    error: str | None = None
    reply_excerpt: str | None = None
    conversation_id: int | None = None


class TaskRunRow(BaseRecordMixin):
    __tablename__ = "books_task_runs"
    __table_args__ = (Index("ix_books_task_runs_task_started", "task_id", "started_at"),)

    task_id: Mapped[int] = mapped_column(
        ForeignKey("books_tasks.id", ondelete="CASCADE"), nullable=False
    )
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[BaseTime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[BaseTime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=TaskRunStatus.RUNNING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("books_conversations.id", ondelete="SET NULL"), nullable=True
    )


class TaskRunBook(BaseBook[TaskRun]):
    """Internal collection for :class:`TaskRun` rows."""

    record_cls = TaskRun
    row_cls = TaskRunRow
