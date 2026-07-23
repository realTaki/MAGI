"""ORM models for scheduled tasks.

Two tables back the "定时/循环任务" feature (see
``magi/agent/proactive/``):

- ``tasks``        : one row per operator-defined task
- ``task_runs``    : one row per execution attempt (cron or manual)

Why these live in :mod:`magi.agent.proactive` instead of
:meth:`magi.agent.db.engine` directly: the proactive
runtime is a new module family; keeping the schema near the
runtime code makes "what does this table serve?" obvious in
a single ``grep``. The Base class is still imported from
:mod:`magi.agent.db.orm` so the SQLite file is shared
and ``init_orm`` can ``create_all`` these tables alongside
``employees`` / ``chat_sessions`` etc.

Schema versioning follows the existing C1.1 model:
``Base.metadata.create_all`` for first-deploy, plus entries
in ``_INDEX_MIGRATIONS`` (see :mod:`magi.agent.db.orm`)
to upgrade pre-existing DBs to the new index set when
these tables are added later.

Columns / defaults
------------------

- ``id`` is a Crockford ULID (same as ``chat_sessions.session_id``).
  ``str(ULID())`` returns the canonical 26-char form; we use
  ``new_ulid()`` from :mod:`magi.agent.memory.session` to keep
  one helper across the codebase.
- Timestamps are ISO UTC strings (``datetime.utcnow().isoformat()``),
  matching the convention in ``magi.agent.session``.
  Avoids timezone-aware datetime round-trips through SQLite
  (which has no native tz support).
- ``enabled`` is an ``Integer`` 0/1 (not Boolean) for
  consistency with the rest of the schema (``chat_messages.archived``,
  ``departments.deleted_at``).

Cross-table FKs
---------------

- ``tasks.uid`` → ``employees.id`` ``RESTRICT``: a task
  references its operator's credentials. Cascade delete would
  wipe history when an admin gets removed (and re-added), so
  we block the delete at the DB level — the action_items
  pattern is the same.
- ``task_runs.session_id`` → ``chat_sessions.session_id``
  ``SET NULL``: deleting the chat session doesn't delete the
  run record (operational visibility wins over tidiness).
"""

from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Re-use the project's shared Base so ``init_orm`` /
# ``create_all`` see these tables on the same MetaData —
# critical for SQLite's single-file-per-DB layout.
from magi.agent.db import Base


class Task(Base):
    """One scheduled task.

    A task is the operator-facing unit of work: a prompt
    that the system runs at cron-driven intervals, charging
    the operator's credentials. Created via the WebUI or
    the LLM-callable ``schedule_task`` tool. Re-runs are
    logged in :class:`TaskRun` rows.
    """

    __tablename__ = "tasks"

    # 26-char Crockford ULID (matches ``chat_sessions.session_id``)
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    # Operator-facing label. Surfaced in the WebUI table + used
    # by the ``schedule_task`` tool to provide idempotent
    # upserts ("update this task", not "create another").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # The full natural-language instruction. Stored verbatim —
    # no templating, no placeholder substitution; if the
    # operator wants variable content they should rewrite
    # the prompt.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Standard 5-field cron: ``min hour day month dow``.
    # Validated by ``proactive.cron_utils.validate_cron`` at
    # the API/tool boundaries; an invalid value is a 400,
    # not silent fallback.
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    # ISO datetime (UTC or with explicit offset) for ONE-SHOT
    # tasks created with ``frequency="once"``. Nullable:
    # recurring rows keep ``NULL``. The scheduler picks
    # ``CronTrigger`` vs ``DateTrigger`` based on which of
    # ``cron`` / ``run_at`` is populated.
    #
    # The two columns are intentionally NOT mutually nulled
    # at the DB level — we'd rather have a single
    # ``one-of-cron-or-run_at`` invariant in app code than
    # a CHECK constraint SQLite has to live with forever.
    # ``schedule_task`` tool + API path both validate that
    # the caller picks one and only one before INSERT/UPDATE.
    run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # IANA name recorded at write-time as a forensic
    # breadcrumb (audit-trail style — "what system tz
    # was in force when this row was created?"). The
    # runtime **ignores** this column: every fire reads
    # the operator's current ``system.timezone`` setting
    # so changing the global tz moves every task to
    # the new local-time schedule without touching
    # each row. Default UTC keeps existing rows
    # readable; we still require the column to be
    # populated (the API writes it on every insert).
    tz: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    # "webui" or "tg". Same closed set as ``chat_sessions.channel``;
    # pinned to one of two strings to keep the channel wiring
    # in the runner simple.
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # Delivery destination — semantic depends on ``channel``:
    #
    #   channel="telegram" → TG tgid (string of digits);
    #                        ``None`` ⇒ use the operator's
    #                        bound ``Employee.telegram_id``.
    #   channel="webui"    → Either the literal string
    #                        ``"new"`` (fire into a fresh chat
    #                        session per fire) or a persisted
    #                        chat session_id (fire into
    #                        *that* specific chat).
    #                        ``None`` for cron-driven rows
    #                        created before delivery_to
    #                        landed — runner falls back to
    #                        the operator's most-recent
    #                        chat session.
    #   channel="email"    → email address (forward-compat;
    #                        runner doesn't ship yet).
    #
    # Why both ``channel`` AND ``delivery_to`` rather than
    # a single ``target`` enum: the WebUI and LLM surfaces
    # think in terms of "where does this fire go"; the
    # runner branches on ``channel``. Splitting them keeps
    # "transport" (channel) and "address" (delivery_to)
    # orthogonal — adding new transports doesn't require
    # touching the address vocabulary.
    delivery_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Agent's home session — the cron-driven conversation
    # that accumulates every fire's prompt + reply.
    # Allocated at task creation time (see ``create_task``
    # in the API + ``schedule_task`` tool); the runner
    # reads this column at fire time instead of resolving
    # a session per fire. ``channel="task"`` for every
    # task; ``tgid`` on the row carries the IM target
    # (TG tgid digits) for the runner's TG-push
    # wiring — but the session itself is never a TG chat.
    #
    # SET NULL on delete: task deletion is a separate
    # operator action that intentionally leaves the
    # session row in place as a record (the session
    # still belongs to the employee, the task_id is
    # gone, the conversation is just labelled).
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )
    # Owner — whose credentials to charge at run time. FK
    # is RESTRICT because deleting an admin should require
    # first removing their tasks (mirrors the action_items
    # pattern).
    uid: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # 0/1 — kept as Integer for schema consistency.
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # ``runner`` increments this on each failure and clears
    # it on success. Crossing the threshold (default 5,
    # ``task.failure_threshold`` in the KV) disables the
    # task and posts an ActionItem for the operator.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Operational denormalised fields — the latest run is
    # the operator's most common "how is it going?" question,
    # so we surface it directly. Authoritative state stays
    # in ``task_runs``; this is a convenience for the table
    # view + the dashboard's "last status" pill.
    last_run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # "success" | "failed" | "running"
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Human-readable reason for the most recent failure —
    # surfaced in the ActionItem the runner posts. Single
    # string so the table cell can show it; full traceback
    # stays in the logs.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ISO UTC strings. Same convention as ``chat_sessions``.
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    # Read-only back-refs — the API serialises these through
    # the TaskOut Pydantic model, never directly. ``runs``
    # is the audit trail; ordered most-recent-first to keep
    # the table view's "Last run" cell aligned with the
    # top of the history pane.
    runs: Mapped[list["TaskRun"]] = relationship(
        back_populates="task",
        viewonly=True,
        order_by="TaskRun.started_at.desc()",
        cascade="all, delete-orphan",
    )
    employee: Mapped["Employee"] = relationship(viewonly=True)

    __table_args__ = (
        # Surface name collisions early — if two operators
        # try to claim the same ``schedule_task`` name
        # we want a clean UNIQUE failure rather than two
        # rows both firing simultaneously.
        UniqueConstraint("name", name="uq_tasks_name"),
        # The scheduler boot scan keys on (enabled=1,
        # next_fire <= now) — but we don't store next_fire
        # here (apscheduler owns it). The index is
        # "enabled + last_run_at" because the operator's
        # "what's running right now" view filters by
        # enabled=1 ordered by last_run_at desc.
        Index("ix_tasks_enabled_last_run", "enabled", "last_run_at"),
        # Per-operator listings ("which of MY tasks are
        # scheduled?").
        Index("ix_tasks_employee", "uid"),
    )


class TaskRun(Base):
    """One execution attempt of a task.

    A row is created at the moment the runner starts (status='running'),
    then updated in place when it completes. The trigger column
    distinguishes cron-driven from manual ``POST /tasks/{id}/run``
    fires — useful for the operator's "was that my test fire?"
    question after clicking Run Now.
    """

    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The chat session this run produced. Nullable + SET NULL
    # so a future "compact historical sessions" sweep can
    # drop the session rows without orphaning the runs.
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )

    # "cron" | "manual"
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)

    # ISO UTC strings
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # "running" | "success" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Single-line error summary. Full exception trace lives
    # in logs; we cap at ~500 chars to keep the cell sane.
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Truncated agent reply — the operator's "what did it
    # say last time?" answer. 500 chars is enough to show
    # enough of the response without flooding the table
    # cell; full reply lives in the chat session linked via
    # ``session_id``.
    reply_excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Token usage for this run. The agent loop writes per-
    # call rows in the ``token_usage`` table; the runner
    # sums those rows into these two columns for the
    # operator's "how much does this cost me?" glance.
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    task: Mapped["Task"] = relationship(back_populates="runs", viewonly=True)
    session: Mapped["ChatSession | None"] = relationship(viewonly=True)

    __table_args__ = (
        # The history pane reads "task_runs WHERE task_id = ?
        # ORDER BY started_at DESC" so the composite index
        # keeps the read at O(log n) for typical N <= 200
        # rows per task.
        Index("ix_task_runs_task_started", "task_id", "started_at"),
    )
