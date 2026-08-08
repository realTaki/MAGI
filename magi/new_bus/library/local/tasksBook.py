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

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal, Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


def _new_task_id() -> str:
    """Mint a fresh task primary key.

    Mirrors the old bus's ``task_<uuid-hex>`` format. Not a
    ULID — the new bus's schema only declared the column as
    ``String(26)`` for forward compat, but the existing
    production data uses 38-char ``task_<hex>`` rows. We
    keep that shape so live data + test fixtures don't have
    to widen the column.
    """
    return f"task_{uuid.uuid4().hex}"


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

# Column-length invariants. Mirror the ORM column
# declarations (``String(120)`` / ``Text``). The Book
# enforces them so every caller — chat-driven tool,
# dashboard API, future agent loop — gets the same
# validation without re-implementing length checks.
NAME_MAX = 120
PROMPT_MAX = 8000


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Task:
    """Unified task definition — user-created OR preset template.

    The ``source`` field discriminates:

    - ``SOURCE_USER`` — rows created by the ``schedule_task`` tool
      (or seeded via dashboard). Have an owning ``uid`` and
      runtime bookkeeping (``last_run_at`` / ``last_status`` /
      ``consecutive_failures``).
    - ``SOURCE_PROACTIVE`` — preset templates bundled from
      ``prompts/task_presets/``. Have a stable ``key``; no
      owning ``uid``.

    The schedule is stored in ONE of two shapes — never both,
    never neither (enforced by :meth:`TaskBook.add`):

    - ``cron`` — a 5-field cron string for RECURRING tasks.
      Consumed verbatim by apscheduler's ``CronTrigger``.
    - ``run_at`` — an ISO 8601 timestamp for ONE-SHOT tasks.
      Consumed verbatim by apscheduler's ``DateTrigger``.

    Conversion from the LLM-facing structured form
    (``frequency`` / ``hour`` / ``minute`` / ``day_of_*``)
    happens at the input boundary (see :func:`preset_to_cron`),
    not at storage. The Book refuses to persist the
    structured form — there's one schedule, not two.
    """

    id: str
    name: str
    prompt: str
    source: str
    target_channel: str
    enabled: int = 1

    # --- schedule (cron XOR run_at, never both) ---------------------------
    cron: str | None = None
    run_at: str | None = None
    tz: str = "UTC"
    delivery_to: str | None = None
    session_id: str | None = None

    # --- proactive-only: stable preset key -------------------------------
    key: str | None = None

    # --- user-task ownership ----------------------------------------------
    uid: int | None = None

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

    # --- schedule (cron XOR run_at, never both) ----------------------------
    cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tz: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC",
    )
    delivery_to: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- proactive-only: stable preset key ---------------------------------
    key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True,
    )

    # --- user-task ownership -----------------------------------------------
    uid: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=True,
    )

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

    def list_by_user(self, *, uid: int) -> list[Task]:
        """User-defined tasks owned by ``uid``.

        Preset rows (source=SOURCE_PROACTIVE) are excluded —
        they have no owning uid and live on
        :meth:`list_proactive_tasks`.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.uid == uid,
                    _TaskRow.source == SOURCE_USER,
                )
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_proactive_tasks(self, *, uid: int) -> list[Task]:
        """Per-user enabled proactive templates.

        ``uid`` is REQUIRED for strict per-user privacy — a
        no-filter scan would leak templates another operator
        shouldn't see. ``uid IS NULL`` rows
        (system-bundled presets from ``prompts/task_presets/``)
        are visible to every uid; ``uid IS NOT NULL`` rows
        (user-private presets) are visible only to the
        matching uid.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.source == SOURCE_PROACTIVE,
                    _TaskRow.enabled == 1,
                    or_(
                        _TaskRow.uid.is_(None),
                        _TaskRow.uid == uid,
                    ),
                )
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self, *, uid: int) -> list[Task]:
        """Per-user enabled tasks (``uid`` + ``enabled=1``).

        ``uid`` is REQUIRED for strict per-user privacy — a
        no-filter scan would leak another operator's rows.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.uid == uid,
                    _TaskRow.source == SOURCE_USER,
                    _TaskRow.enabled == 1,
                )
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, source: str = SOURCE_USER, **kwargs) -> Task:
        """Insert one task row.

        Owns the write invariants applicable to *any* task:
        ``name`` non-empty + ≤120 chars, ``prompt`` non-empty
        + ≤8000 chars, ``target_channel`` in the closed
        :class:`ChannelEnum` set, ``source`` in
        :data:`ALL_SOURCES`, schedule is exactly one of
        ``cron`` (validated via apscheduler) or ``run_at``
        (validated as ISO 8601 + canonicalised + must be
        in the future). Every caller — chat-driven tool,
        dashboard API, future agent loop — gets the same
        validation without re-implementing length checks
        or schedule parsing.

        Callers pass ``source=`` explicitly: chat-driven tools
        pass :data:`SOURCE_USER`, preset bundlers pass
        :data:`SOURCE_PROACTIVE`. The default
        (:data:`SOURCE_USER`) is the safe side: a writer that
        forgets to tag is treated as a user task, which can be
        edited by its owner.

        Raises :class:`ValueError` on invariant violation;
        translators (the tool worker, the dashboard route
        handler) catch and surface as ``is_error=True`` /
        4xx.

        Note: per-frequency / per-preset translation
        (``frequency`` → ``cron``) lives at the input
        boundary (the LLM tool's structured-form → cron
        conversion happens BEFORE this method); this Book
        only sees the canonical ``cron`` or ``run_at``
        shape and validates IT.
        """
        self._validate_write_invariants(
            name=kwargs.get("name") or "",
            prompt=kwargs.get("prompt") or "",
            target_channel=kwargs.get("target_channel"),
            source=source,
        )
        # Schedule is one shape, not two — ``cron`` XOR
        # ``run_at``, never both, never neither. Validation
        # runs HERE (at the Book boundary) so any caller —
        # chat-driven tool, dashboard API, future agent loop
        # — gets the same parse + future-check without
        # re-implementing them at every entry point.
        cron_val = kwargs.get("cron")
        run_at_val = kwargs.get("run_at")
        if (cron_val is None) == (run_at_val is None):
            raise ValueError(
                "exactly one of cron (recurring) or run_at "
                "(one-shot) must be set; got "
                f"cron={cron_val!r}, run_at={run_at_val!r}"
            )
        if cron_val is not None:
            try:
                validate_cron(cron_val)
            except ValueError as e:
                raise ValueError(f"cron is not a valid expression: {e}") from None
        else:
            # ``run_at`` path: ISO-parse + canonicalise (so
            # "+08:00" / "Z" / naive UTC all land on the same
            # canonical string in SQLite) + reject past times
            # so an apscheduler ``DateTrigger`` that would
            # silently drop the job never reaches the DB.
            try:
                canonical = validate_run_at(run_at_val)
            except ValueError as e:
                raise ValueError(
                    f"run_at is not a valid ISO 8601 timestamp: {e}"
                ) from None
            try:
                validate_run_at_future(canonical)
            except ValueError as e:
                raise ValueError(
                    f"run_at {canonical!r} is in the past: {e}"
                ) from None
            kwargs["run_at"] = canonical
        with self._session() as s:
            row = _TaskRow(source=source, **kwargs)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def disable(self, *, task_id: str, uid: int) -> bool:
        """Disable a task — owner-only.

        Requires ``uid`` for strict per-user privacy: a row
        whose ``uid`` doesn't match is silently skipped
        (returns ``False``) so callers can't probe for
        other operators' ``task_id`` values via
        success/failure timing. ``True`` on a successful
        disable (whether the row was already disabled or
        just flipped); ``False`` when the row is missing
        OR the row is owned by a different uid.

        Proactive templates (``source=SOURCE_PROACTIVE``)
        have no owning uid and aren't covered by this
        primitive — disable them via direct DB update or a
        system-internal helper. The dispatcher / admin
        tools can reach for those; LLM-driven tools
        cannot.
        """
        with self._session() as s:
            row = s.scalar(
                select(_TaskRow).where(
                    _TaskRow.id == task_id,
                    _TaskRow.uid == uid,
                )
            )
            if row is None:
                return False
            row.enabled = 0
            s.commit()
            return True

    def get_by_name(self, *, name: str) -> Task | None:
        """Lookup-by-name helper.

        Lets callers (chat-driven tool, dashboard API)
        decide between update and insert at the call site.
        :meth:`upsert_by_name` composes this with
        :meth:`add` for the common case.
        """
        with self._session() as s:
            row = s.scalar(
                select(_TaskRow).where(_TaskRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def upsert_by_name(
        self,
        *,
        name: str,
        prompt: str,
        cron: str | None,
        run_at: str | None,
        delivery_to: str | None,
        target_channel: str,
        uid: int,
        session_id: str,
        tz: str,
        enabled: int = 1,
    ) -> tuple[str, bool]:
        """Idempotent upsert keyed on the unique ``name`` column.

        The LLM retries the ``schedule_task`` tool often on
        transient errors; without this primitive a retry
        would either 500 on the unique-index conflict or
        silently create a duplicate row. Same shape the
        WebUI task API uses, so any caller updating a task
        by its human-readable label gets one code path.

        Returns ``(task_id, is_update)`` — the existing
        ``task_id`` and ``is_update=True`` when the name
        matched a row; a freshly minted ``task_id`` and
        ``is_update=False`` on insert. ``session_id`` is
        sticky on update (preserves conversation continuity
        across prompt edits); the caller-supplied one
        sticks only on the insert path.

        ``run_at`` is canonicalised (ISO 8601 parsed + UTC
        normalised) here on BOTH branches so a writer that
        passes "2026-08-01T07:30:00+00:00" and another
        that passes "2026-08-01T15:30:00+08:00" both end
        up updating the same row to the same canonical
        string. Past-time ``run_at`` is rejected on both
        branches — an apscheduler job that would silently
        drop at fire-time never reaches the DB regardless
        of whether the path is update or insert.

        Authorisation is the caller's responsibility (the
        LLM tool passes ``ctx.uid``; the API passes the
        admin's id) — the Book is pure data.
        """
        # Validate the schedule at the Book boundary so any
        # caller (LLM tool, dashboard API, future agent
        # loop) gets the same parse + future-check on
        # BOTH the update and insert branches.
        canonical_run_at: str | None = None
        if run_at is not None:
            try:
                canonical_run_at = validate_run_at(run_at)
            except ValueError as e:
                raise ValueError(
                    f"run_at is not a valid ISO 8601 timestamp: {e}"
                ) from None
            try:
                validate_run_at_future(canonical_run_at)
            except ValueError as e:
                raise ValueError(
                    f"run_at {canonical_run_at!r} is in the past: {e}"
                ) from None
        elif cron is not None:
            # ``cron`` validation lives in :meth:`add` for
            # the insert branch; mirror it here for the
            # update branch so a tool that updates a row's
            # cron to garbage also fails fast.
            try:
                validate_cron(cron)
            except ValueError as e:
                raise ValueError(f"cron is not a valid expression: {e}") from None
        else:
            raise ValueError(
                "exactly one of cron (recurring) or run_at "
                "(one-shot) must be set; got both None"
            )
        # Length / enum invariants are enforced by
        # :meth:`add`; this primitive shares the same
        # validation contract on the insert branch — we
        # duplicate the tiny check block rather than call
        # ``add()`` here, because the outer session is
        # already open and a nested ``add()`` session would
        # trip SQLite's "transaction within a transaction"
        # guard.
        self._validate_write_invariants(
            name=name, prompt=prompt, target_channel=target_channel,
            source=SOURCE_USER,
        )
        with self._session() as s:
            existing = s.scalar(
                select(_TaskRow).where(_TaskRow.name == name)
            )
            if existing is not None:
                existing.prompt = prompt
                existing.cron = cron
                existing.run_at = canonical_run_at
                existing.delivery_to = delivery_to
                existing.target_channel = target_channel
                existing.enabled = enabled
                existing.uid = uid
                # Preserve the existing ``session_id`` for
                # continuity across prompt edits. Update-
                # path only — insert path uses the caller-
                # supplied value.
                if existing.session_id is None:
                    existing.session_id = session_id
                s.commit()
                s.refresh(existing)
                return existing.id, True

            # Insert path — single session, single
            # transaction. The write invariants above
            # already passed; this is the same row that
            # ``add()`` would have built.
            now = utcnow_naive().isoformat()
            insert = _TaskRow(
                id=_new_task_id(),
                name=name,
                prompt=prompt,
                cron=cron,
                run_at=canonical_run_at,
                delivery_to=delivery_to,
                session_id=session_id,
                tz=tz,
                target_channel=target_channel,
                uid=uid,
                enabled=enabled,
                source=SOURCE_USER,
                created_at=now,
                updated_at=now,
            )
            s.add(insert)
            s.commit()
            s.refresh(insert)
            return insert.id, False

    def _validate_write_invariants(
        self, *, name: str, prompt: str,
        target_channel: str | None, source: str,
    ) -> None:
        """Length / enum checks shared by :meth:`add` and
        :meth:`upsert_by_name`. Lives in one place so a
        future invariant (column-width cap change, new
        enum member) only needs to land once.

        The schedule-specific validation (cron XOR run_at,
        cron expression validity, run_at ISO + future
        check) lives directly in the callers since the
        two branches need to differ slightly (update
        doesn't need cron XOR run_at because the existing
        row already satisfied it).
        """
        if not name or not str(name).strip():
            raise ValueError("name must be a non-empty string")
        if len(str(name)) > NAME_MAX:
            raise ValueError(
                f"name length {len(str(name))} exceeds maximum {NAME_MAX}"
            )
        if not prompt or not str(prompt).strip():
            raise ValueError("prompt must be a non-empty string")
        if len(str(prompt)) > PROMPT_MAX:
            raise ValueError(
                f"prompt length {len(str(prompt))} exceeds "
                f"maximum {PROMPT_MAX}"
            )
        if target_channel is not None and target_channel not in set(ChannelEnum):
            raise ValueError(
                f"target_channel must be one of "
                f"{sorted(c.value for c in ChannelEnum)!r}, "
                f"got {target_channel!r}"
            )
        if source not in ALL_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(ALL_SOURCES)!r}, "
                f"got {source!r}"
            )

    # -- v2.0: worker-facing methods -------------------------------------

    def record_run_start(
        self, *, task_id: str, trigger: str,
        run_id: str | None = None,
    ) -> TaskRun:
        """Insert a task_runs row, write task.last_run_at.

        trigger ∈ closed set:
          'cron_tick' | 'run_at_consume' | 'manual_run' |
          'api_manual_run' | 'schedule_task_tool'
        """
        run_id = run_id or uuid.uuid4().hex
        started_at = utcnow_naive().isoformat()
        with self._session() as s:
            run_row = _TaskRunRow(
                id=run_id, task_id=task_id, trigger=trigger,
                started_at=started_at, status="running",
            )
            s.add(run_row)
            task = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
            if task is not None:
                task.last_run_at = started_at
            s.commit()
            return self._row_to_dto(run_row)

    def mark_run_at_consumed(self, *, task_id: str) -> None:
        """One-shot run_at: set enabled=0 after successful fire."""
        with self._session() as s:
            task = s.scalar(select(_TaskRow).where(_TaskRow.id == task_id))
            if task is None:
                return
            task.enabled = 0
            s.commit()

    def list_all_enabled_for_workers(self) -> list[Task]:
        """Per-user scan across all uids — workers only path.

        The uid-scoped list_enabled(uid) is preserved for user-facing UI;
        this primitive scans every user's enabled USER tasks for the cron
        poll loop.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.enabled == 1,
                    _TaskRow.source == SOURCE_USER,
                )
            ).all()
            return [self._row_to_dto(r) for r in rows]


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

    def reap_stale(self, *, older_than_seconds: int = 300) -> int:
        """Flip stuck 'running' rows to 'failed'. Returns count.

        Used by TaskWorker on startup for crash recovery.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        ).isoformat()
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRunRow).where(
                    _TaskRunRow.status == "running",
                    _TaskRunRow.started_at < cutoff,
                )
            ).all()
            for row in rows:
                row.status = "failed"
                row.error = "abandoned by previous worker"
                row.finished_at = utcnow_naive().isoformat()
            s.commit()
            return len(rows)


# -- schedule helpers ----------------------------------------------------
#
# Cron + run_at validation/formatting helpers. These used to live
# in their own module (``taskSchedule.py``); they merged in here
# because every caller — the ``schedule_task`` LLM tool, the
# WebUI's "next fire" preview, the API layer — goes through the
# same shape (cron string OR ISO ``run_at``) and the Book is the
# canonical owner of the schema. Co-locating the helpers with
# the Book means there's one import for both the CRUD primitives
# and the schedule validators.


CronFrequency = Literal["hourly", "daily", "weekly", "monthly", "once"]


# ── cron path — recurring tasks ────────────────────────────────────────


def validate_cron(expr: str) -> None:
    """Raise ``ValueError`` if ``expr`` isn't valid 5-field cron.

    Uses ``croniter`` for validation — no apscheduler dependency.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("cron expression must be a non-empty string")
    # croniter validates during construction
    from croniter import croniter

    croniter(expr.strip())


def next_fire(expr: str, tz: str = "UTC") -> Optional[datetime]:
    """Return the next fire time of ``expr`` in ``tz``.

    Returns ``None`` on bad input (the API / tool layer
    should have validated first; this is a defensive
    fallback for callers like the WebUI that want to
    preview a fire time without round-tripping through
    the API).
    """
    from croniter import croniter

    try:
        croniter(expr)  # validate
    except (ValueError, KeyError):
        return None
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    now = datetime.now(timezone.utc).astimezone(zone)
    # croniter.get_next returns naive datetime in the expression's implied tz
    return croniter(expr, now).get_next(datetime)


def humanize_cron(expr: str) -> str:
    """Render a one-line English phrase for ``expr``.

    Covers the common cases (``* * * * *``, ``0 9 * * *``, ``*/5 * * * *``,
    weekday/weekend blocks). For complex expressions falls back to raw string.

    Uses ``croniter`` for validation; field parsing is manual (croniter
    doesn't expose structured fields like apscheduler's ``CronTrigger.fields``).
    """
    from croniter import croniter

    try:
        croniter(expr)  # validate
    except (ValueError, KeyError):
        return expr or "(empty)"

    # Parse the 5-field cron string manually
    parts = expr.strip().split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts

    all_star = all(v in ("*", None) for v in (minute, hour, dom, month, dow))
    if all_star:
        return "Every minute"

    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "Every hour"
    if (
        dom == "*"
        and month == "*"
        and dow == "*"
        and minute.isdigit()
        and hour.isdigit()
    ):
        return f"Every day at {int(hour):02d}:{int(minute):02d}"
    if dom == "*" and month == "*":
        if dow == "mon-fri":
            return (
                f"Weekdays at {_format_hhmm(hour, minute)}"
                if not (minute == "*" and hour == "*")
                else "Weekdays, every minute"
            )
        if dow == "sat,sun":
            return (
                f"Weekends at {_format_hhmm(hour, minute)}"
                if not (minute == "*" and hour == "*")
                else "Weekends, every minute"
            )
    return expr


def _format_hhmm(hour: str, minute: str) -> str:
    try:
        return f"{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        return f"{hour}:{minute}"


def preset_to_cron(
    frequency: CronFrequency,
    *,
    hour: int = 0,
    minute: int = 0,
    day_of_week: Optional[int] = None,
    day_of_month: Optional[int] = None,
) -> str:
    """Render the LLM-facing structured form into a 5-field cron.

    Storage layer only sees the cron string — this conversion
    happens at the input boundary (LLM tool, preset bundler).
    Mapping (minute / hour / day / month / dow):

    - hourly:  ``M  * * * *`` — fires every minute the hour rolls.
                 Caller passes ``minute`` for "fire at minute X
                 past every hour"; hour is ignored.
    - daily:   ``M H * * *`` — fires once at HH:MM every day.
    - weekly:  ``M H * * DOW`` — fires once at HH:MM on one DOW
                 (Python ``datetime.weekday()``, 0=Mon..Sun=6;
                 cron uses 0=Sun..6=Sat so we translate).
    - monthly: ``M H DOM * *`` — fires once at HH:MM on the
                 given DOM (1..31).

    Hour must be 0..23, minute 0..59, DOM 1..31, DOW 0..6
    (``weekday()`` style with Monday=0; we shift to cron style
    on output). Invalid combinations raise ``ValueError``.

    For one-shot tasks use :func:`validate_run_at` and store
    the result in ``run_at`` — do NOT call this with
    ``frequency='once'``.
    """
    if not (0 <= int(minute) <= 59):
        raise ValueError(f"minute must be 0..59, got {minute!r}")
    if not (0 <= int(hour) <= 23):
        raise ValueError(f"hour must be 0..23, got {hour!r}")
    m = int(minute)
    h = int(hour)
    if frequency == "hourly":
        return f"{m} * * * *"
    if frequency == "daily":
        return f"{m} {h} * * *"
    if frequency == "weekly":
        if day_of_week is None:
            raise ValueError("weekly preset requires day_of_week (0..6, Mon=0)")
        if not (0 <= int(day_of_week) <= 6):
            raise ValueError(f"day_of_week must be 0..6, got {day_of_week!r}")
        cron_dow = (int(day_of_week) + 1) % 7
        return f"{m} {h} * * {cron_dow}"
    if frequency == "monthly":
        if day_of_month is None:
            raise ValueError("monthly preset requires day_of_month (1..31)")
        if not (1 <= int(day_of_month) <= 31):
            raise ValueError(f"day_of_month must be 1..31, got {day_of_month!r}")
        return f"{m} {h} {int(day_of_month)} * *"
    raise ValueError(f"unknown frequency: {frequency!r}")


# ── run_at path — one-shot tasks ───────────────────────────────────────


def validate_run_at(raw: str) -> str:
    """Validate the ``run_at`` field for a one-shot task.

    Accepts any ISO 8601 timestamp that ``datetime.fromisoformat``
    can parse — offset-aware or naive UTC. Naive timestamps
    are interpreted as UTC, matching the project's "UTC in
    DB" convention.

    Returns the **canonical** ISO string (rounded-to-second,
    UTC ``Z`` suffix) so two operators who write
    ``"2026-08-01T07:30:00+00:00"`` and
    ``"2026-08-01T15:30:00+08:00"`` both end up storing the
    same row. ``apscheduler.DateTrigger`` accepts the
    returned string verbatim.

    The ``Z`` suffix (rather than ``+00:00``) matches the
    project-wide convention used by
    :mod:`magi.new_bus.library.local.actionItemBook`,
    :mod:`magi.new_bus.library.local.sessionBook`, and
    the WebUI's serializer — every wire shape in MAGI uses
    trailing ``Z`` for UTC, never ``+00:00``.

    Raises ``ValueError`` on any parse failure.

    Note: this helper does NOT enforce "must be in the
    future" — see :func:`validate_run_at_future`.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("run_at must be a non-empty ISO 8601 string")
    candidate = raw.strip()
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as e:
        raise ValueError(
            f"run_at {raw!r} is not a parseable ISO 8601 timestamp: {e}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    # UTC ``Z`` suffix, not ``+00:00`` — see docstring.
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def validate_run_at_future(run_at_iso: str, *, now: datetime | None = None) -> str:
    """Reject past-time ``run_at`` so a silently-dropped
    apscheduler job never reaches the DB.

    apscheduler's ``DateTrigger`` returns ``None`` from
    ``get_next_fire_time`` when ``run_date`` is in the past
    at registration time — the job sits in the jobstore
    forever. Rejecting here surfaces the same fact at
    create-time.

    A small grace window (60 s) absorbs clock skew between
    the operator's browser, the WebUI server, and the DB
    host — a request that arrives 30 s "late" still
    schedules, but a request that's an hour late doesn't
    silently succeed.

    Returns the input unchanged on success. Raises
    :class:`ValueError` with the parsed value + server "now".
    """
    parsed = datetime.fromisoformat(run_at_iso)
    server_now = now or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    grace_seconds = 60
    if parsed <= server_now - timedelta(seconds=grace_seconds):
        raise ValueError(
            f"run_at must be in the future (got {run_at_iso!r}; "
            f"server now is {server_now.isoformat(timespec='seconds')}; "
            f"past-time jobs are silently dropped by apscheduler)"
        )
    return run_at_iso


__all__ = [
    "ALL_SOURCES",
    "Channel",
    "ChannelEnum",
    "CronFrequency",
    "SOURCE_PROACTIVE",
    "SOURCE_USER",
    "Task",
    "TaskBook",
    "TaskRun",
    "TaskRunBook",
    "humanize_cron",
    "next_fire",
    "preset_to_cron",
    "validate_cron",
    "validate_run_at",
    "validate_run_at_future",
    "_TaskRow",
    "_TaskRunRow",
]
