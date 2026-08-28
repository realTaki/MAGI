"""TaskBook — durable scheduled-task definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


class TaskSource(StrEnum):
    """Where a task definition came from."""

    USER = "user"
    PROACTIVE = "proactive"


@dataclass(kw_only=True)
class Task(BaseRecord):
    """One scheduled task definition.

    The future Task Jobs own schedule validation and mutation; this Book owns
    only the durable row shape.
    """

    name: str
    prompt: str
    source: TaskSource = TaskSource.USER
    enabled: bool = True
    cron: str | None = None
    conversation_id: int | None = None


class TaskRow(BaseRecordMixin):
    __tablename__ = "books_tasks"
    __table_args__ = (
        UniqueConstraint("name", name="uq_books_tasks_name"),
        Index("ix_books_tasks_source", "source"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default=TaskSource.USER.value)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("books_conversations.id", ondelete="SET NULL"), nullable=True
    )


class TaskBook(BaseBook[Task]):
    """Internal collection for :class:`Task` rows."""

    record_cls = Task
    row_cls = TaskRow
