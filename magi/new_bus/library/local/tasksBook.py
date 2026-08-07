"""TaskBook + TaskRunBook — scheduled task domain.

Two tables:
- ``tasks``     — one row per task DEFINITION (user-created OR
                  preset template). The ``source`` field
                  (SOURCE_USER | SOURCE_PROACTIVE) tells them
                  apart; both shapes share a single ORM row.
- ``task_runs`` — one row per execution attempt.

Also home to :class:`ChannelEnum` — the cross-package vocabulary
for ``Task.target_channel`` (and the dispatcher's
``delivery_channel``). Lives here because a task's *delivery
target* IS a channel; the enum is the closed set of values
``target_channel`` may take.

Schema mirrors the old bus's ``tasks`` + ``task_runs`` +
``task_presets``, collapsed from three tables to two by the
proactive/user ``source`` discriminator (parallel to the
``action_items`` refactor).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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


# -- public enums --------------------------------------------------------


class ChannelEnum(StrEnum):
    """Closed set of values for ``Task.target_channel``.

    A task's *delivery target* is a channel — the surface where
    the fired reply surfaces to the operator. The ORM column is
    ``String(16)`` (loose, not enum-enforced at the DB layer) so
    legacy rows survive the migration; new writes must use one of
    these values. The dispatcher / channel adapters import this
    enum rather than reaching for free-form strings.

    Mirrors the old bus's
    :class:`magi.bus.jobs.protocols.channels.Channel` so
    cross-package callers (channel adapters, agent runner,
    ``schedule_task`` tool) can migrate one symbol at a time.
    """

    TG = "tg"
    """Telegram bot channel — operator's bound TG chat."""

    WEBUI = "webui"
    """WebUI chat console — operator's dashboard history."""

    A2A = "a2a"
    """Agent-to-Agent channel — MAGI peer exchange."""

    SCHEDULED = "scheduled"
    """Internal scheduled-task channel — fires on a cron / run_at."""


# Back-compat alias.  ``Channel`` is the historical name used by
# the channel adapters and the agent runner; keeping it pointing
# at the same class lets those modules migrate independently of
# the tools layer.
Channel = ChannelEnum


# Provenance tag — propagated onto ``Task.source`` so the
# dashboard / runner can group rows by origin (operator-driven
# vs bundled preset). Mirrors the ``action_items`` precedent:
# the unified table collapses the old "user task" / "task
# preset" distinction into one ``source`` discriminator.
SOURCE_USER = "user"
SOURCE_PROACTIVE = "proactive"
ALL_SOURCES = frozenset({SOURCE_USER, SOURCE_PROACTIVE})


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Task:
    """Unified task definition — user-created OR preset template.

    The ``source`` field discriminates:

    - ``SOURCE_USER`` — rows created by the ``schedule_task`` tool
      (or seeded via dashboard). Have an owning ``uid``, an
      apscheduler-ready ``cron``, and runtime bookkeeping
      (``last_run_at`` / ``last_status`` / ``consecutive_failures``).

    - ``SOURCE_PROACTIVE`` — preset templates bundled from
      ``prompts/task_presets/``. Have a stable ``key`` plus a
      structured ``frequency`` / ``hour`` / ``minute`` /
      ``day_of_*`` schedule (the dashboard's structured form);
      no owning ``uid``.

    Schedule fields are nullable where they don't apply: a preset
    uses the structured form, a user task uses ``cron``. Both
    shapes are persisted to the same ``tasks`` table.
    """

    id: str
    name: str
    prompt: str
    source: str
    target_channel: str
    enabled: int = 1

    # --- user-task schedule (apscheduler form) ----------------------------
    cron: str | None = None
    tz: str = "UTC"
    run_at: str | None = None
    delivery_to: str | None = None
    session_id: str | None = None

    # --- preset template schedule (structured dashboard form) ------------
    key: str | None = None
    frequency: str | None = None
    hour: int | None = None
    minute: int | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None

    # --- user-task ownership + linkage ------------------------------------
    uid: int | None = None
    preset_id: str | None = None
    preset_key: str | None = None

    # --- user-task runtime bookkeeping ------------------------------------
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


# -- internal ORM --------------------------------------------------------


class _TaskRow(Base):
    __tablename__ = "tasks"
    # ``scheduleTaskNotify`` (in ``magi.new_bus.guild``) registers
    # the same Table for its fire-and-forget path; whichever
    # module is imported first wins, and the other must opt-in.
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_USER,
    )
    target_channel: Mapped[str] = mapped_column(
        "channel", String(16), nullable=False,
    )
    enabled: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    # --- user-task schedule -------------------------------------------------
    cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tz: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC",
    )
    run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_to: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- preset template schedule ------------------------------------------
    key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True,
    )
    frequency: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
    )
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- user-task ownership + linkage -------------------------------------
    uid: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=True,
    )
    preset_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    preset_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- user-task runtime bookkeeping -------------------------------------
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
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
        Index("ix_tasks_source", "source"),
        # ``scheduleTaskNotify`` registers the same Table for
        # its fire-and-forget path; combined in one tuple so
        # the second declaration doesn't shadow the first.
        # SQLAlchemy convention: dict kwargs must come last.
        {"extend_existing": True},
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


# -- Books ---------------------------------------------------------------


class TaskBook(BaseBook[_TaskRow, Task]):
    """CRUD for the unified ``tasks`` table.

    ``source`` discriminates user-created tasks
    (:data:`SOURCE_USER`) from preset templates
    (:data:`SOURCE_PROACTIVE`); the row shape is the same. The
    Book refuses ``add()`` calls whose ``source`` isn't in
    :data:`ALL_SOURCES` — same convention as
    :class:`~magi.new_bus.library.local.actionItemBook`.
    """

    model_cls = _TaskRow
    dto_cls = Task

    def get(self, *, task_id: str) -> Task | None:
        with self._session() as s:
            row = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
            return self._row_to_dto(row) if row else None

    def list_for_owner(self, *, uid: int) -> list[Task]:
        """User-defined tasks owned by ``uid``.

        Preset rows (source=SOURCE_PROACTIVE) are excluded —
        they have no owning uid and live on
        :meth:`list_presets`.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.uid == uid,
                    _TaskRow.source == SOURCE_USER,
                )
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_presets(self) -> list[Task]:
        """Enabled preset templates (source=SOURCE_PROACTIVE).

        Mirrors the old ``TaskPresetBook.list_enabled`` API on
        top of the unified table.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.source == SOURCE_PROACTIVE,
                    _TaskRow.enabled == 1,
                )
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self) -> list[Task]:
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(_TaskRow.enabled == 1)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, source: str = SOURCE_USER, **kwargs) -> Task:
        """Insert one task row.

        Callers pass ``source=`` explicitly: chat-driven tools
        pass :data:`SOURCE_USER`, preset bundlers pass
        :data:`SOURCE_PROACTIVE`. The default
        (:data:`SOURCE_USER`) is the safe side: a writer that
        forgets to tag is treated as a user task, which can be
        edited by its owner.
        """
        if source not in ALL_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(ALL_SOURCES)!r}, "
                f"got {source!r}"
            )
        with self._session() as s:
            row = _TaskRow(source=source, **kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def disable(self, *, task_id: str) -> None:
        with self._session() as s:
            row = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
            if row is None:
                return
            row.enabled = 0
            s.commit()


class TaskRunBook(BaseBook[_TaskRunRow, TaskRun]):
    model_cls = _TaskRunRow
    dto_cls = TaskRun

    def get(self, *, run_id: str) -> TaskRun | None:
        with self._session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.id == run_id))
            return self._row_to_dto(row) if row else None

    def list_for_task(self, *, task_id: str) -> list[TaskRun]:
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRunRow)
                .where(_TaskRunRow.task_id == task_id)
                .order_by(_TaskRunRow.started_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, **kwargs) -> TaskRun:
        with self._session() as s:
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
        with self._session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.id == run_id))
            if row is None:
                return
            row.status = status
            row.error = error
            row.reply_excerpt = reply_excerpt
            row.finished_at = finished_at
            row.latency_ms = latency_ms
            s.commit()


__all__ = [
    "ALL_SOURCES",
    "Channel",
    "ChannelEnum",
    "SOURCE_PROACTIVE",
    "SOURCE_USER",
    "Task",
    "TaskBook",
    "TaskRun",
    "TaskRunBook",
    "_TaskRow",
    "_TaskRunRow",
]
