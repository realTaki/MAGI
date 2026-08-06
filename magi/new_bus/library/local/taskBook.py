"""TaskBook + TaskRunBook + TaskPresetBook — scheduled task domain.

Three tables:
- ``tasks``         — one row per operator-defined task
- ``task_runs``     — one row per execution attempt
- ``task_presets``  — one row per preset template (operator-curated)

Schema mirrors the old bus's ``tasks`` + ``task_runs`` + ``task_presets``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    name: str
    prompt: str
    cron: str
    uid: int
    target_channel: str
    run_at: str | None = None
    tz: str = "UTC"
    delivery_to: str | None = None
    session_id: str | None = None
    preset_id: str | None = None
    preset_key: str | None = None
    enabled: int = 1
    consecutive_failures: int = 0
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class TaskRun:
    id: str
    task_id: str
    trigger: str
    started_at: str
    finished_at: str | None = None
    latency_ms: int | None = None
    status: str = "running"
    error: str | None = None
    reply_excerpt: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskPreset:
    id: str
    key: str
    name: str
    prompt: str
    frequency: str
    hour: int
    minute: int
    day_of_week: int | None = None
    day_of_month: int | None = None
    run_at: str | None = None
    target_channel: str = "webui"
    enabled: int = 1


# -- internal ORM --------------------------------------------------------


class _TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tz: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    target_channel: Mapped[str] = mapped_column(
        "channel", String(16), nullable=False
    )
    delivery_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    preset_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_presets.id", ondelete="SET NULL"), nullable=True
    )
    preset_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_tasks_name"),
        Index("ix_tasks_enabled_last_run", "enabled", "last_run_at"),
        Index("ix_tasks_contact", "uid"),
        Index("ix_tasks_preset_key", "preset_key"),
    )


class _TaskRunRow(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reply_excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_task_runs_task_started", "task_id", "started_at"),
    )


class _TaskPresetRow(Base):
    __tablename__ = "task_presets"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# -- Books ---------------------------------------------------------------


class TaskBook(BaseBook[_TaskRow, Task]):
    model_cls = _TaskRow
    dto_cls = Task

    def get(self, *, task_id: str) -> Task | None:
        with self._factory.session() as s:
            row = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
            return self._row_to_dto(row) if row else None

    def list_for_owner(self, *, uid: int) -> list[Task]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TaskRow).where(_TaskRow.uid == uid)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self) -> list[Task]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TaskRow).where(_TaskRow.enabled == 1)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, **kwargs) -> Task:
        with self._factory.session() as s:
            row = _TaskRow(**kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def disable(self, *, task_id: str) -> None:
        with self._factory.session() as s:
            row = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
            if row is None:
                return
            row.enabled = 0
            s.commit()


class TaskRunBook(BaseBook[_TaskRunRow, TaskRun]):
    model_cls = _TaskRunRow
    dto_cls = TaskRun

    def get(self, *, run_id: str) -> TaskRun | None:
        with self._factory.session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.id == run_id))
            return self._row_to_dto(row) if row else None

    def list_for_task(self, *, task_id: str) -> list[TaskRun]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TaskRunRow)
                .where(_TaskRunRow.task_id == task_id)
                .order_by(_TaskRunRow.started_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, **kwargs) -> TaskRun:
        with self._factory.session() as s:
            row = _TaskRunRow(**kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def complete(self, *, run_id: str, status: str,
                 error: str | None = None,
                 reply_excerpt: str | None = None,
                 finished_at: str = "",
                 latency_ms: int | None = None) -> None:
        with self._factory.session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.id == run_id))
            if row is None:
                return
            row.status = status
            row.error = error
            row.reply_excerpt = reply_excerpt
            row.finished_at = finished_at
            row.latency_ms = latency_ms
            s.commit()


class TaskPresetBook(BaseBook[_TaskPresetRow, TaskPreset]):
    model_cls = _TaskPresetRow
    dto_cls = TaskPreset

    def get(self, *, preset_id: str) -> TaskPreset | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_TaskPresetRow).where(_TaskPresetRow.id == preset_id)
            )
            return self._row_to_dto(row) if row else None

    def list_enabled(self) -> list[TaskPreset]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TaskPresetRow).where(_TaskPresetRow.enabled == 1)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, **kwargs) -> TaskPreset:
        with self._factory.session() as s:
            row = _TaskPresetRow(**kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


__all__ = [
    "Task",
    "TaskRun",
    "TaskPreset",
    "TaskBook",
    "TaskRunBook",
    "TaskPresetBook",
    "_TaskRow",
    "_TaskRunRow",
    "_TaskPresetRow",
]
