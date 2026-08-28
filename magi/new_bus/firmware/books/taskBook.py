"""TaskBook — durable scheduled-task definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime


class TaskSource(StrEnum):
    """Where a task definition came from."""

    USER = "user"
    PROACTIVE = "proactive"


class TaskRunStatus(StrEnum):
    """Terminal and in-progress states shared by tasks and their runs."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(kw_only=True)
class Task(BaseRecord):
    """One scheduled task definition.

    A task has exactly one schedule: a recurring ``cron`` expression or a
    one-shot ``run_at`` timestamp.  The future Task Jobs own validation and
    mutation; this Book owns only the durable row shape.
    """

    name: str
    prompt: str
    target_channel: str
    source: TaskSource = TaskSource.USER
    enabled: bool = True
    cron: str | None = None
    run_at: BaseTime | None = None
    tz: str = "UTC"
    delivery_to: str | None = None
    conversation_id: int | None = None
    contact_id: int | None = None
    consecutive_failures: int = 0
    last_run_at: BaseTime | None = None
    last_status: TaskRunStatus | None = None
    last_error: str | None = None


class TaskRow(BaseRecordMixin):
    __tablename__ = "books_tasks"
    __table_args__ = (
        UniqueConstraint("name", name="uq_books_tasks_name"),
        CheckConstraint(
            "(cron IS NOT NULL AND run_at IS NULL) OR (cron IS NULL AND run_at IS NOT NULL)",
            name="ck_books_tasks_schedule",
        ),
        Index("ix_books_tasks_enabled_last_run", "enabled", "last_run_at"),
        Index("ix_books_tasks_contact", "contact_id"),
        Index("ix_books_tasks_source", "source"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    target_channel: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default=TaskSource.USER.value)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_at: Mapped[BaseTime | None] = mapped_column(DateTime, nullable=True)
    tz: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    delivery_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("books_conversations.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="RESTRICT"), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[BaseTime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskBook(BaseBook[Task]):
    """Internal collection for :class:`Task` rows."""

    record_cls = Task
    row_cls = TaskRow
