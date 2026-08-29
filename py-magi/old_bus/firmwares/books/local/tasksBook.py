"""TaskBook + TaskRunBook — scheduled task domain.

Two tables:
- ``tasks``     — one row per task DEFINITION (user-created OR
                  preset template). The ``source`` field
                  (TaskSource.USER | TaskSource.PROACTIVE) tells them
                  apart; both shapes share a single ORM row.
- ``task_runs`` — one row per execution attempt.

``Task.target_channel`` is a persisted channel-name string.  Channel
capabilities are registered dynamically in ``settings.channels.available``;
this task domain must not own a stale closed Enum vocabulary.

Schema for ``tasks`` + ``task_runs`` (collapsed from three
tables to two by the proactive/user ``source`` discriminator,
parallel to the ``action_items`` refactor).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from old_bus.bases.db.base import enum_column, utcnow_naive
from old_bus.firmwares.books.local.conversationBook import _ConversationRow


def _new_task_id() -> str:
    """Mint a compact opaque identity for API and job boundaries."""

    return uuid.uuid4().hex[:26]


# Provenance tag — propagated onto ``Task.source`` so the
# dashboard / runner can group rows by origin (operator-driven
# vs bundled preset). Mirrors the ``action_items`` precedent:
# the unified table collapses the old "user task" / "task
# preset" distinction into one ``source`` discriminator.
class TaskSource(StrEnum):
    """Closed set of task provenance values."""

    USER = "user"
    PROACTIVE = "proactive"


# Runtime status of a task execution attempt. Shared by
# :attr:`TaskRun.status` (per-run ledger) AND
# :attr:`Task.last_status` (denormalised onto the parent task so
# the dashboard doesn't have to join the ``task_runs`` table for
# the "✓ 成功 / ✗ 失败" cell). One enum, one vocabulary — a
# split here would let a row's ``last_status`` and the matching
# ``task_runs`` row drift into inconsistent values, which the
# WebUI can't render coherently. Vocabulary matches the WebUI's
# existing checks (``TaskListPane.tsx``).
class TaskRunStatus(StrEnum):
    """Closed set of values for ``TaskRun.status`` + ``Task.last_status``.

    Stored as native ENUM via :func:`bus.bases.db.base.enum_column` (PG) / CHECK (SQLite).
    ``StrEnum`` keeps raw-string ↔ enum value equivalence so callers
    can pass either shape without a coercion shim.
    """

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Task(BaseRecord):
    """Unified task definition — user-created OR preset template.

    The ``source`` field discriminates:

    - ``TaskSource.USER`` — rows created by the ``schedule_task`` tool
      (or seeded via dashboard). Have an owning ``contact_id`` and
      runtime bookkeeping (``last_run_at`` / ``last_status`` /
      ``consecutive_failures``).
    - ``TaskSource.PROACTIVE`` — preset templates bundled from
      ``prompts/proactive/``. No owning ``contact_id``.

    The schedule is stored in ONE of two shapes — never both,
    never neither (enforced by :meth:`TaskBook.add`):

    - ``cron`` — a 5-field cron string for RECURRING tasks.
      Consumed verbatim by apscheduler's ``CronTrigger``.
    - ``run_at`` — a naive-UTC ``datetime`` for ONE-SHOT tasks.
      Consumed directly by apscheduler's ``DateTrigger``.

    Conversion from the LLM-facing structured form
    (``frequency`` / ``hour`` / ``minute`` / ``day_of_*``)
    happens at the input boundary (see :func:`preset_to_cron`),
    not at storage. The Book refuses to persist the
    structured form — there's one schedule, not two.
    """

    task_id: str = field(default_factory=_new_task_id)
    name: str  # 任务唯一名
    prompt: str  # 触发后执行的 prompt
    source: TaskSource = TaskSource.USER  # 来源（user/proactive）
    target_channel: str  # 投递渠道（由 settings.channels.available 注册）
    enabled: int = 1

    # --- schedule (cron XOR run_at, never both) ---------------------------
    cron: str | None = None  # 周期表达式（5 字段）
    run_at: datetime | None = None
    tz: str = "UTC"
    delivery_to: str | None = None
    conversation_id: int | None = None  # 关联会话 id（chat_conversations.id）

    # --- user-task ownership ----------------------------------------------
    contact_id: int | None = None

    # --- user-task runtime bookkeeping ------------------------------------
    consecutive_failures: int = 0
    last_run_at: datetime | None = None
    last_status: TaskRunStatus | None = None  # 最近一次状态
    last_error: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskRun(BaseRecord):
    run_id: str = field(default_factory=_new_task_id)
    task_id: str
    manual: bool
    started_at: datetime = field(default_factory=utcnow_naive)
    finished_at: datetime | None = None
    latency_ms: int | None = None
    status: TaskRunStatus = TaskRunStatus.RUNNING  # 运行状态（running/success/failed）
    error: str | None = None
    reply_excerpt: str | None = None
    conversation_id: int | None = None  # 关联会话 id（chat_conversations.id）


# -- internal ORM --------------------------------------------------------


class _TaskRow(BaseRecordMixin):
    __tablename__ = "tasks"
    # ``scheduleTaskNotify`` (in ``bus.firmwares.jobs``) registers
    # the same Table for its fire-and-forget path; whichever
    # module is imported first wins, and the other must opt-in.
    __table_args__ = {"extend_existing": True}

    task_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[TaskSource] = mapped_column(
        enum_column(TaskSource),
        nullable=False,
        default=TaskSource.USER,
    )
    target_channel: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    # --- schedule (cron XOR run_at, never both) ----------------------------
    cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tz: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="UTC",
    )
    delivery_to: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation: Mapped[_ConversationRow | None] = relationship(lazy="joined")

    # --- user-task ownership -----------------------------------------------
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # --- user-task runtime bookkeeping -------------------------------------
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[TaskRunStatus | None] = mapped_column(
        enum_column(TaskRunStatus), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("name", name="uq_tasks_name"),
        Index("ix_tasks_enabled_last_run", "enabled", "last_run_at"),
        Index("ix_tasks_contact", "contact_id"),
        Index("ix_tasks_source", "source"),
        # ``scheduleTaskNotify`` registers the same Table for
        # its fire-and-forget path; combined in one tuple so
        # the second declaration doesn't shadow the first.
        # SQLAlchemy convention: dict kwargs must come last.
        {"extend_existing": True},
    )


class _TaskRunRow(BaseRecordMixin):
    __tablename__ = "task_runs"

    run_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True
    )
    manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[TaskRunStatus] = mapped_column(enum_column(TaskRunStatus), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    task: Mapped[_TaskRow] = relationship(lazy="joined")
    conversation: Mapped[_ConversationRow | None] = relationship(lazy="joined")

    __table_args__ = (Index("ix_task_runs_task_started", "task_id", "started_at"),)


# -- Books ---------------------------------------------------------------


class TaskBook(BaseBook[_TaskRow, Task]):
    """CRUD for the unified ``tasks`` table.

    ``source`` discriminates user-created tasks
    (:attr:`TaskSource.USER`) from preset templates
    (:attr:`TaskSource.PROACTIVE`); the row shape is the same. The
    Book refuses ``add()`` calls whose ``source`` isn't in
    :class:`TaskSource` — same convention as
    :class:`~bus.firmwares.books.local.actionItemBook`.
    """

    model_cls = _TaskRow
    record_cls = Task

    def get_by_task_id(self, *, task_id: str) -> Task | None:
        with self._session() as s:
            row = s.scalar(select(_TaskRow).where(_TaskRow.task_id == task_id))
            return self.record_cls.from_row(row) if row else None

    def list_by_user(self, *, contact_id: int) -> list[Task]:
        """User-defined tasks owned by ``contact_id``.

        Preset rows (source=TaskSource.PROACTIVE) are excluded —
        they have no owning contact_id and live on
        :meth:`list_proactive_tasks`.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.contact_id == contact_id,
                    _TaskRow.source == TaskSource.USER,
                )
            ).all()
            return [self.record_cls.from_row(r) for r in rows]

    def list_proactive_tasks(self, *, contact_id: int) -> list[Task]:
        """Per-user enabled proactive templates.

        ``contact_id`` is REQUIRED for strict per-user privacy — a
        no-filter scan would leak templates another operator
        shouldn't see. ``contact_id IS NULL`` rows
        (system-bundled presets from ``prompts/proactive/``)
        are visible to every contact_id; ``contact_id IS NOT NULL`` rows
        (user-private presets) are visible only to the
        matching contact_id.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.source == TaskSource.PROACTIVE,
                    _TaskRow.enabled == 1,
                    or_(
                        _TaskRow.contact_id.is_(None),
                        _TaskRow.contact_id == contact_id,
                    ),
                )
            ).all()
            return [self.record_cls.from_row(r) for r in rows]

    def list_enabled(self, *, contact_id: int) -> list[Task]:
        """Per-user enabled tasks (``contact_id`` + ``enabled=1``).

        ``contact_id`` is REQUIRED for strict per-user privacy — a
        no-filter scan would leak another operator's rows.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.contact_id == contact_id,
                    _TaskRow.source == TaskSource.USER,
                    _TaskRow.enabled == 1,
                )
            ).all()
            return [self.record_cls.from_row(r) for r in rows]

    def _validate_add(self, record: Task) -> None:
        """Validate the DTO accepted by the generic ``BaseBook.add``."""
        # Schedule is one shape, not two — ``cron`` XOR
        # ``run_at``, never both, never neither. Validation
        # runs HERE (at the Book boundary) so any caller —
        # chat-driven tool, dashboard API, future agent loop
        # — gets the same parse + future-check without
        # re-implementing them at every entry point.
        if not isinstance(record.target_channel, str) or not record.target_channel.strip():
            raise ValueError("target_channel must be a non-empty channel name")
        cron_val = record.cron
        run_at_val = record.run_at
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
            # ``run_at_val`` is guaranteed non-None here by the
            # XOR check at line 408; the ``assert`` documents the
            # invariant for the type checker (and trips loudly if
            # someone breaks the XOR later).
            assert run_at_val is not None
            # ``run_at`` is already a ``datetime`` by the Record contract;
            # canonicalise aware values to naive UTC and reject past times
            # so an apscheduler ``DateTrigger`` that would
            # silently drop the job never reaches the DB.
            assert run_at_val is not None
            try:
                canonical = validate_run_at(run_at_val)
            except ValueError as e:
                raise ValueError(f"run_at is not a valid datetime: {e}") from None
            try:
                validate_run_at_future(canonical)
            except ValueError as e:
                raise ValueError(f"run_at {canonical!r} is in the past: {e}") from None

    def _record_to_row_values(self, record: Task, session) -> dict:
        """Map the DTO onto row columns.

        ``conversation_id`` maps one-to-one to the FK column; the session is
        used only to surface a friendly error when the reference dangles.
        """
        run_at = validate_run_at(record.run_at) if record.run_at is not None else None
        if record.conversation_id is not None and session.get(
            _ConversationRow, record.conversation_id
        ) is None:
            raise ValueError(f"unknown conversation_id {record.conversation_id!r}")
        return {
            "task_id": record.task_id,
            "name": record.name,
            "prompt": record.prompt,
            "source": record.source,
            "target_channel": record.target_channel,
            "enabled": record.enabled,
            "cron": record.cron,
            "run_at": run_at,
            "tz": record.tz,
            "delivery_to": record.delivery_to,
            "conversation_id": record.conversation_id,
            "contact_id": record.contact_id,
            "consecutive_failures": record.consecutive_failures,
            "last_run_at": record.last_run_at,
            "last_status": record.last_status,
            "last_error": record.last_error,
        }

    def disable(self, *, task_id: str, contact_id: int) -> bool:
        """Disable a task — owner-only.

        Requires ``contact_id`` for strict per-user privacy: a row
        whose ``contact_id`` doesn't match is silently skipped
        (returns ``False``) so callers can't probe for
        other operators' ``task_id`` values via
        success/failure timing. ``True`` on a successful
        disable (whether the row was already disabled or
        just flipped); ``False`` when the row is missing
        OR the row is owned by a different contact_id.

        Proactive templates (``source=TaskSource.PROACTIVE``)
        have no owning contact_id and aren't covered by this
        primitive — disable them via direct DB update or a
        system-internal helper. The dispatcher / admin
        tools can reach for those; LLM-driven tools
        cannot.
        """
        with self._session() as s:
            row = s.scalar(
                select(_TaskRow).where(
                    _TaskRow.task_id == task_id,
                    _TaskRow.contact_id == contact_id,
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
            row = s.scalar(select(_TaskRow).where(_TaskRow.name == name))
            return self.record_cls.from_row(row) if row else None

    def upsert_by_name(
        self,
        *,
        name: str,
        prompt: str,
        cron: str | None,
        run_at: str | datetime | None,
        delivery_to: str | None,
        target_channel: str,
        contact_id: int,
        conversation_id: int,
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
        ``is_update=False`` on insert. ``conversation_id`` is
        sticky on update (preserves conversation continuity
        across prompt edits); the caller-supplied one
        sticks only on the insert path.

        ``run_at`` accepts either an ISO-8601 string (from an
        HTTP/tool payload) or a ``datetime`` (from backend
        code) and is canonicalised to naive UTC on BOTH
        branches via :func:`validate_run_at`. Past-time
        ``run_at`` is rejected on both branches — an
        apscheduler job that would silently drop at
        fire-time never reaches the DB regardless of whether
        the path is update or insert.

        Authorisation is the caller's responsibility (the
        LLM tool passes ``ctx.contact_id``; the API passes the
        admin's id) — the Book is pure data.
        """
        # Validate the schedule at the Book boundary so any
        # caller (LLM tool, dashboard API, future agent
        # loop) gets the same parse + future-check on
        # BOTH the update and insert branches.
        canonical_run_at: datetime | None = None
        if run_at is not None:
            try:
                canonical_run_at = validate_run_at(run_at)
            except ValueError as e:
                raise ValueError(f"run_at is not a valid datetime: {e}") from None
            try:
                validate_run_at_future(canonical_run_at)
            except ValueError as e:
                raise ValueError(f"run_at {canonical_run_at!r} is in the past: {e}") from None
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
                "exactly one of cron (recurring) or run_at (one-shot) must be set; got both None"
            )
        candidate = Task(
            name=name,
            prompt=prompt,
            source=TaskSource.USER,
            target_channel=target_channel,
            enabled=enabled,
            cron=cron,
            run_at=canonical_run_at,
            tz=tz,
            delivery_to=delivery_to,
            conversation_id=conversation_id,
            contact_id=contact_id,
        )
        with self._session() as s:
            existing = s.scalar(select(_TaskRow).where(_TaskRow.name == candidate.name))
            if existing is not None:
                existing.prompt = candidate.prompt
                existing.cron = candidate.cron
                existing.run_at = candidate.run_at
                existing.delivery_to = candidate.delivery_to
                existing.target_channel = candidate.target_channel
                existing.enabled = candidate.enabled
                existing.contact_id = candidate.contact_id
                # Preserve the existing ``conversation_id`` for
                # continuity across prompt edits. Update-
                # path only — insert path uses the caller-
                # supplied value.
                if existing.conversation_id is None:
                    conversation = s.scalar(
                        select(_ConversationRow).where(
                            _ConversationRow.id == candidate.conversation_id
                        )
                    )
                    if conversation is None:
                        raise ValueError(f"unknown conversation_id {candidate.conversation_id!r}")
                    existing.conversation_id = candidate.conversation_id
                s.commit()
                s.refresh(existing)
                return existing.task_id, True

            conversation = s.scalar(
                select(_ConversationRow).where(
                    _ConversationRow.id == candidate.conversation_id
                )
            )
            if conversation is None:
                raise ValueError(f"unknown conversation_id {candidate.conversation_id!r}")

            # Insert path — single session, single
            # transaction. The write invariants above
            # already passed; this is the same row that
            # ``add()`` would have built.
            insert = _TaskRow(
                task_id=candidate.task_id,
                name=candidate.name,
                prompt=candidate.prompt,
                cron=candidate.cron,
                run_at=candidate.run_at,
                delivery_to=candidate.delivery_to,
                conversation_id=candidate.conversation_id,
                tz=candidate.tz,
                target_channel=candidate.target_channel,
                contact_id=candidate.contact_id,
                enabled=candidate.enabled,
                source=candidate.source,
            )
            s.add(insert)
            s.commit()
            s.refresh(insert)
            return insert.task_id, False

    # -- v2.0: worker-facing methods -------------------------------------

    def record_run_start(
        self,
        *,
        task_id: str,
        manual: bool,
        run_id: str | None = None,
    ) -> TaskRun:
        """Insert a task_runs row, write task.last_run_at.

        ``manual=True`` 表示用户/工具主动触发（API / UI / tool）；
        ``False`` 表示 task 模块按自身规则（cron / run_at）触发。
        与 :class:`~bus.firmwares.jobs.runTaskJob.RunTaskJob.manual`
        同构。
        """
        new_run_id = run_id or _new_task_id()
        started_at = utcnow_naive()
        with self._session() as s:
            task = s.scalar(select(_TaskRow).where(_TaskRow.task_id == task_id))
            if task is None:
                raise ValueError(f"unknown task_id {task_id!r}")
            run_row = _TaskRunRow(
                run_id=new_run_id,
                task_id=task.task_id,
                conversation_id=task.conversation_id,
                manual=manual,
                started_at=started_at,
                status=TaskRunStatus.RUNNING.value,
            )
            s.add(run_row)
            task.last_run_at = started_at
            s.commit()
            s.refresh(run_row)
        # ``self.record_cls`` is ``Task``, not ``TaskRun`` — convert the
        # run row via ``TaskRun.from_row`` so the field set matches.
        return TaskRun.from_row(run_row)

    def mark_run_at_consumed(self, *, task_id: str) -> None:
        """One-shot run_at: set enabled=0 after successful fire."""
        with self._session() as s:
            task = s.scalar(select(_TaskRow).where(_TaskRow.task_id == task_id))
            if task is None:
                return
            task.enabled = 0
            s.commit()

    def list_all_enabled_for_workers(self) -> list[Task]:
        """Per-user scan across all contact_ids — workers only path.

        The contact_id-scoped list_enabled(contact_id) is preserved for user-facing UI;
        this primitive scans every user's enabled USER tasks for the cron
        poll loop.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).where(
                    _TaskRow.enabled == 1,
                    _TaskRow.source == TaskSource.USER,
                )
            ).all()
            return [self.record_cls.from_row(r) for r in rows]


class TaskRunBook(BaseBook[_TaskRunRow, TaskRun]):
    model_cls = _TaskRunRow
    record_cls = TaskRun

    def get_by_run_id(self, *, run_id: str) -> TaskRun | None:
        with self._session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.run_id == run_id))
            return self.record_cls.from_row(row) if row else None

    def _record_to_row_values(self, record: TaskRun, session) -> dict:
        """Map the DTO onto row columns.

        The owning task must exist (``unknown task_id``); when the run does
        not name its own conversation, it inherits the task's. Both checks
        fail fast with a friendly error instead of a DB constraint violation.
        """
        task = session.scalar(select(_TaskRow).where(_TaskRow.task_id == record.task_id))
        if task is None:
            raise ValueError(f"unknown task_id {record.task_id!r}")
        if record.conversation_id is not None and session.get(
            _ConversationRow, record.conversation_id
        ) is None:
            raise ValueError(f"unknown conversation_id {record.conversation_id!r}")
        return {
            "run_id": record.run_id,
            "task_id": record.task_id,
            "conversation_id": (
                record.conversation_id
                if record.conversation_id is not None
                else task.conversation_id
            ),
            "manual": record.manual,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "latency_ms": record.latency_ms,
            "status": record.status,
            "error": record.error,
            "reply_excerpt": record.reply_excerpt,
        }

    def complete(
        self,
        *,
        run_id: str,
        status: TaskRunStatus | str,
        error: str | None = None,
        reply_excerpt: str | None = None,
        finished_at: datetime | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Mark a run as finished with a terminal ``status``.

        ``status`` accepts either a :class:`TaskRunStatus` enum
        (preferred — :data:`TaskRunStatus.SUCCESS` /
        :data:`TaskRunStatus.FAILED`) or the equivalent bare
        string. Strings go through :class:`TaskRunStatus` so a typo
        (``"succes"``) raises :class:`ValueError` immediately rather
        than persisting silently. The ``status`` column is
        SAEnum-typed, so SQLAlchemy accepts the enum member
        directly on write.
        """
        normalised = status if isinstance(status, TaskRunStatus) else TaskRunStatus(status)
        with self._session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.run_id == run_id))
            if row is None:
                return
            row.status = normalised
            row.error = error
            row.reply_excerpt = reply_excerpt
            row.finished_at = finished_at
            row.latency_ms = latency_ms
            s.commit()

    def reap_stale(self, *, older_than_seconds: int = 300) -> int:
        """Flip stuck ``RUNNING`` rows to ``FAILED``. Returns count.

        Used by TaskWorker on startup for crash recovery.
        """
        if older_than_seconds <= 0:
            raise ValueError("older_than_seconds must be positive")
        cutoff = utcnow_naive() - timedelta(seconds=older_than_seconds)
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRunRow).where(
                    _TaskRunRow.status == TaskRunStatus.RUNNING.value,
                    _TaskRunRow.started_at < cutoff,
                )
            ).all()
            for row in rows:
                # ``enum_column`` stores ``.value`` via ``values_callable``,
                # so writing the enum member is equivalent to writing
                # ``TaskRunStatus.FAILED.value`` and stays type-correct
                # (``row.status: Mapped[TaskRunStatus]``).
                row.status = TaskRunStatus.FAILED
                row.error = "abandoned by previous worker"
                row.finished_at = utcnow_naive()
            s.commit()
            return len(rows)


# -- schedule helpers ----------------------------------------------------
#
# Cron + run_at validation/formatting helpers. These used to live
# in their own module (``taskSchedule.py``); they merged in here
# because every caller — the ``schedule_task`` LLM tool, the
# WebUI's "next fire" preview, the API layer — goes through the
# same shape (cron string OR ``datetime`` ``run_at``) and the Book is the
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


def next_fire(expr: str, tz: str = "UTC") -> datetime | None:
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
    now = datetime.now(UTC).astimezone(zone)
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
    if dom == "*" and month == "*" and dow == "*" and minute.isdigit() and hour.isdigit():
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
    day_of_week: int | None = None,
    day_of_month: int | None = None,
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


def validate_run_at(raw: str | datetime) -> datetime:
    """Normalize a one-shot ``run_at`` value to naive UTC ``datetime``.

    This is an ingress-boundary helper: it accepts an ISO-8601 string from an
    HTTP/tool payload or a ``datetime`` from backend code. Persisted ``Task``
    Records only accept ``datetime``. Naive timestamps are interpreted as UTC.

    Returns a naive UTC ``datetime`` so two operators who write
    ``"2026-08-01T07:30:00+00:00"`` and
    ``"2026-08-01T15:30:00+08:00"`` both end up storing the
    same row. The Book stores this native value in its ``DateTime`` column.

    Raises ``ValueError`` on any parse failure.

    Note: this helper does NOT enforce "must be in the
    future" — see :func:`validate_run_at_future`.
    """
    if isinstance(raw, datetime):
        parsed = raw
    else:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("run_at must be a non-empty ISO 8601 string")
        candidate = raw.strip()
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as e:
            raise ValueError(f"run_at {raw!r} is not a parseable ISO 8601 timestamp: {e}") from None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def validate_run_at_future(run_at: datetime, *, now: datetime | None = None) -> datetime:
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
    server_now = now or utcnow_naive()
    if server_now.tzinfo is not None:
        server_now = server_now.astimezone(UTC).replace(tzinfo=None)
    grace_seconds = 60
    if run_at <= server_now - timedelta(seconds=grace_seconds):
        raise ValueError(
            f"run_at must be in the future (got {run_at!r}; "
            f"server now is {server_now!s}; "
            f"past-time jobs are silently dropped by apscheduler)"
        )
    return run_at


__all__ = [
    "CronFrequency",
    "Task",
    "TaskBook",
    "TaskRun",
    "TaskRunBook",
    "TaskRunStatus",
    "TaskSource",
    "humanize_cron",
    "next_fire",
    "preset_to_cron",
    "validate_cron",
    "validate_run_at",
    "validate_run_at_future",
    "_TaskRow",
    "_TaskRunRow",
]
