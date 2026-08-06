"""TaskJob + TaskRunJob + TaskPresetJob — writes to scheduled-task tables."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive

logger = logging.getLogger("magi.new_bus.jobs.task")


# -- ORM -----------------------------------------------------------------


class _JTaskRow(JobBase):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str] = mapped_column(nullable=False)
    run_at: Mapped[str | None] = mapped_column(nullable=True)
    tz: Mapped[str] = mapped_column(default="UTC", nullable=False)
    target_channel: Mapped[str] = mapped_column("channel", nullable=False)
    delivery_to: Mapped[str | None] = mapped_column(nullable=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False,
    )
    preset_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_presets.id", ondelete="SET NULL"), nullable=True,
    )
    preset_key: Mapped[str | None] = mapped_column(nullable=True)
    enabled: Mapped[int] = mapped_column(default=1, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(default=0, nullable=False)
    last_run_at: Mapped[str | None] = mapped_column(nullable=True)
    last_status: Mapped[str | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(nullable=False)
    updated_at: Mapped[str] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_tasks_name"),
        Index("ix_tasks_enabled_last_run", "enabled", "last_run_at"),
        Index("ix_tasks_contact", "uid"),
        Index("ix_tasks_preset_key", "preset_key"),
    )


class _JTaskRunRow(JobBase):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"), nullable=True,
    )
    trigger: Mapped[str] = mapped_column(nullable=False)
    started_at: Mapped[str] = mapped_column(nullable=False)
    finished_at: Mapped[str | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False)
    error: Mapped[str | None] = mapped_column(nullable=True)
    reply_excerpt: Mapped[str | None] = mapped_column(nullable=True)
    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)


class _JTaskPresetRow(JobBase):
    __tablename__ = "task_presets"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    key: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(nullable=False)
    hour: Mapped[int] = mapped_column(nullable=False)
    minute: Mapped[int] = mapped_column(nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(nullable=True)
    run_at: Mapped[str | None] = mapped_column(nullable=True)
    target_channel: Mapped[str] = mapped_column(nullable=False)
    enabled: Mapped[int] = mapped_column(default=1, nullable=False)


# -- Job classes ---------------------------------------------------------


class TaskJob(BaseJob):
    """Write side of the task definition domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(self, **kwargs) -> str:
        with self._factory.session() as s:
            row = _JTaskRow(**kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def disable(self, *, task_id: str) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JTaskRow).where(_JTaskRow.id == task_id)
            )
            if row is None:
                return
            row.enabled = 0
            s.commit()


class TaskRunJob(BaseJob):
    """Write side of the task-run audit domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(self, **kwargs) -> str:
        with self._factory.session() as s:
            row = _JTaskRunRow(**kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def complete(
        self,
        *,
        run_id: str,
        status: str,
        error: str | None = None,
        reply_excerpt: str | None = None,
        finished_at: str = "",
        latency_ms: int | None = None,
    ) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JTaskRunRow).where(_JTaskRunRow.id == run_id)
            )
            if row is None:
                return
            row.status = status
            row.error = error
            row.reply_excerpt = reply_excerpt
            row.finished_at = finished_at
            row.latency_ms = latency_ms
            s.commit()


class TaskPresetJob(BaseJob):
    """Write side of the task-preset template domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(self, **kwargs) -> str:
        with self._factory.session() as s:
            row = _JTaskPresetRow(**kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id


__all__ = ["TaskJob", "TaskRunJob", "TaskPresetJob"]
