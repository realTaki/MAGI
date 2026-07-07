"""SQLAlchemy ORM models + bootstrap.

Lives in the same SQLite file as the hand-rolled ``settings`` KV
store (C0). The two coexist: ``settings`` and ``meta`` are written
by the raw-SQL helpers in ``local_db.py`` / ``settings.py``;
everything else uses SQLAlchemy via this module.

We deliberately use ``Base.metadata.create_all`` (not Alembic) for
C1.1 — the schema is small, the tables are new, and adding Alembic
now would double the surface area. The first Alembic baseline
migration lands when the schema stabilises (probably end of C1.3).
Once Alembic is in, this ``init_orm`` becomes the no-op it
already is at runtime — only ``alembic upgrade head`` touches
the schema.

Thread-safety: a single ``Engine`` shared across the process,
``Session`` per request (FastAPI dependency). The engine is
configured with ``check_same_thread=False`` so the TG bot thread
and the uvicorn event loop can both issue queries without
tripping over the default. WAL mode (set in ``init_sqlite``)
handles writer/reader concurrency.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

logger = logging.getLogger("magi.runtime.state.orm")

# Default ``state_dir`` matches the in-container location; tests
# override via the ``MAGI_STATE_DIR`` env var.
_DEFAULT_STATE_DIR = "/workspace/memories"


class Base(DeclarativeBase):
    """Shared declarative base for all ORM tables."""


class Employee(Base):
    """A person in the company.

    Schema kept minimal on purpose — C1.1 has the dept assignment
    + LLM provider config (each employee can route to a different
    model when their EVE handles traffic). C1.2 grows the
    lifecycle (email, status, quiet hours) and C2 adds the
    telegram_id binding.

    ``api_key`` is the employee's LLM-provider key. It is treated
    as a secret — never returned in plain text by any endpoint,
    only as a boolean ``api_key_set`` flag + a ``last4`` suffix
    so the UI can show ``"sk-…abcd"`` without leaking the full
    value. Stored plain-text for C0; the C8 hardening pass
    encrypts at rest with a deployer-supplied ``MAGI_SECRET``.
    """

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Direct FK to the dept the employee belongs to. Nullable:
    # an employee can exist without a department assignment
    # yet (the UI exposes a "未指定部门" pseudo-section that
    # filters on this being NULL).
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # LLM provider. For C1.1 this is a free-text string; C3
    # adds routing logic (Anthropic / OpenAI / Ollama / etc.).
    provider: Mapped[str | None] = mapped_column(String(32))
    # Secret. Never logged, never returned in plain text.
    api_key: Mapped[str | None] = mapped_column(String(512))
    # Soft-delete flag. NULL means active; non-NULL is the
    # timestamp at which the employee was marked separated.
    # Separated employees are hidden by default in department
    # views (toggleable) and exposed via the dedicated
    # "已离职员工" scope — the dashboard never hard-deletes
    # employees because the org needs the historical record
    # (manager_of, past assignments, audit references).
    separated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Role on the MAGI instance — four-value enum:
    #   - "admin"    : operator / deployer. Can sign in to
    #                  Adam's WebUI. Sees the audit log, the
    #                  employee directory, the channel admin.
    #                  The TG bot logs admin messages but
    #                  doesn't run them through the LLM (we
    #                  don't burn the operator's API key on
    #                  chitchat).
    #   - "assigned" : the person this MAGI serves. Their
    #                  TG messages go through the agent loop.
    #                  In v0 single-instance, all "real"
    #                  employees default to ``assigned``.
    #   - "employee" : another company employee. NOT
    #                  served by this MAGI. Cross-MAGI
    #                  access (an employee of company X
    #                  talking to company Y's MAGI) is a
    #                  future concern; for v0 the bot
    #                  politely refuses their messages.
    #   - "guest"    : not in this company at all — a
    #                  visitor. The bot's first-touch
    #                  discovery reply is the path that
    #                  handles this; a row with role='guest'
    #                  is rare and usually operator-created.
    # v0 writes ``admin`` (onboarding) or ``assigned``
    # (dashboard create). ``employee`` and ``guest`` exist
    # so future C6+ (cross-MAGI access, public visitors)
    # can read them without a schema change.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="assigned")
    # Telegram chat id of the bound user, when known. NULL
    # for employees who haven't completed the /start binding
    # flow (C2). Unique across the table — one chat_id
    # binds to at most one employee. Stored on the row so
    # the TG bot can resolve a chat_id to its employee in
    # a single ORM read; the older ``meta``-key mapping
    # (telegram.user.<chat_id>.employee_id) is deprecated
    # but kept in the codebase for back-compat with
    # any state that hasn't been migrated yet.
    # Uniqueness is enforced via the ``ux_employees_telegram_id``
    # index created in :data:`_UNIQUE_INDEX_MIGRATIONS` — a
    # plain ``UNIQUE`` constraint here would block ALTER
    # TABLE on a pre-existing table (SQLite refuses "Cannot
    # add a UNIQUE column"). The index is ``WHERE telegram_id
    # IS NOT NULL`` so multiple NULLs (un-bound employees)
    # don't collide, matching the spirit of SQL UNIQUE.
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # The department this employee belongs to (not to be confused
    # with ``Department.manager`` which is the "lead" pointer).
    # Single FK column + backref from Department.employees so the
    # dashboard can ask "who is in this dept" without a second
    # query.
    department: Mapped["Department | None"] = relationship(
        back_populates="employees",
        foreign_keys=[department_id],
    )

    # The department this employee leads, if any. ``remote_side``
    # disambiguates the two FKs (Department.manager and
    # Department.manager_id) on the parent side.
    led_department: Mapped["Department | None"] = relationship(
        back_populates="manager",
        foreign_keys="Department.manager_id",
    )

    def __repr__(self) -> str:
        return f"Employee(id={self.id}, name={self.name!r}, department_id={self.department_id})"


class Department(Base):
    """A node in the company org tree.

    The tree is encoded by ``parent_id``: top-level departments
    have ``parent_id = NULL``; every other department's parent
    is another department in the same table. Cycles are
    prevented at the API layer (POST/PATCH refuse a parent that
    would close a loop).

    ``manager_id`` references ``employees.id`` and is nullable —
    a department can exist without a manager assigned yet.
    """

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Self-referential tree. ``remote_side=id`` is the magic that
    # tells SQLAlchemy which side of the parent_id FK is the
    # "many" side, so ``children`` is a list of departments
    # rather than a back to the parent.
    children: Mapped[list["Department"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    parent: Mapped["Department | None"] = relationship(
        back_populates="children",
        remote_side="Department.id",
    )

    manager: Mapped["Employee | None"] = relationship(
        back_populates="led_department",
        foreign_keys=[manager_id],
    )

    # Employees that belong to this department. Backref from
    # ``Employee.department``. ``viewonly=True`` so the
    # Department endpoint doesn't accidentally mutate
    # employees via the collection.
    employees: Mapped[list["Employee"]] = relationship(
        back_populates="department",
        foreign_keys="Employee.department_id",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"Department(id={self.id}, name={self.name!r}, parent_id={self.parent_id})"


# -- action items -----------------------------------------------------------
#
# A small "to-do" surface — the dashboard shows these in the
# "Action Items" sidebar entry. The first concrete use case is
# ``kind='llm_credentials_missing'``: every new admin gets one
# so the dashboard nudges them to set their provider + API key
# before they chat. C4 will reuse the same table for EVE-driven
# follow-ups (kind strings like ``eve_followup_meeting``).
#
# Schema is intentionally kind-agnostic — every row carries
# a stable ``kind`` + human-readable ``title`` / ``description``
# / ``target_url``. No ``payload_json`` blob (YAGNI for the
# rows we can foresee; add it later if C4 needs structured
# per-kind fields).
#
# FK policy is ``ON DELETE SET NULL`` rather than CASCADE:
# ``save_admin`` deletes old admin rows; cascade would wipe
# their action history silently. SET NULL leaves an
# ``employee_id IS NULL`` orphan that the post-commit sweep
# in ``save_admin`` re-binds to the freshly-recreated admin,
# so "remove admin and re-add" naturally surfaces the prompt
# again instead of erasing it.


class ActionItem(Base):
    """A to-do surfaced to an admin in the dashboard.

    Created by system paths (``save_admin`` etc.) and, from
    C4, by EVEs that want to nudge the operator about a
    follow-up. Dismissed / completed by the operator via
    ``POST /api/action_items/{id}/complete`` — auto-completion
    is deliberately out of scope (the operator may want to
    dismiss a row for reasons unrelated to the underlying
    state).
    """

    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    # See class docstring on the SET NULL choice. Nullable
    # because the FK target can disappear (admin removed),
    # leaving the action item as an orphan until something
    # re-binds it.
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stable identifier per row category. Free-form string;
    # the schema doesn't enforce a closed enum so C4 can add
    # new kinds without a migration. Picked to be short +
    # readable in /api/action_items?kind=<here> URLs.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    # Where the "去设置" button navigates. Currently a path
    # relative to the dashboard; future in-app tab switches
    # can replace it with a deep-link state.
    target_url: Mapped[str | None] = mapped_column(String(500))
    # "normal" (default) or "high" — C4 uses "high" for
    # time-sensitive follow-ups. Not a closed enum here for
    # the same reason as ``kind``.
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal"
    )
    # Who created the row. "system" for save_admin /
    # similar, "eve" when C4 EVE-driven rows land, "user"
    # for future operator-authored reminders. Useful for
    # grouping + filtering later.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="system"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    # Null = open. The "I clicked 完成" stamp.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    completed_by_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional reason captured at complete-time; useful for
    # C4 EVE-driven rows where the operator may say "I'll do
    # it next week" (the EVE then reads it back).
    completion_note: Mapped[str | None] = mapped_column(String(500))
    # Hidden without recording "I did it". Distinct from
    # completed_at IS NOT NULL — both remove from the open
    # list, but a dismissed row never claims the underlying
    # action was performed. Used by the future "hide this
    # prompt" affordance; v0 leaves dismissed at False.
    dismissed: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Viewonly relationships so route code never mutates
    # Employee via the ActionItem collection.
    employee: Mapped["Employee | None"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )
    completed_by: Mapped["Employee | None"] = relationship(
        foreign_keys=[completed_by_employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"ActionItem(id={self.id}, kind={self.kind!r}, "
            f"employee_id={self.employee_id}, "
            f"completed={self.completed_at is not None}, "
            f"dismissed={self.dismissed})"
        )


class TokenUsage(Base):
    """One row per outbound LLM call.

    Powers the per-employee token-bill aggregation endpoint
    (see ``magi.channels.webui.api.employee_metrics``).
    Permanent: this table is meant to grow indefinitely
    so week/month aggregates stay accurate. The operator-
    facing endpoint ``/api/employees/{id}/token-usage``
    answers the "what did this employee cost?" question;
    the session JSON files (D.6) answer the "what was
    said?" question. v0 doesn't carry a separate audit
    log — those two surfaces cover the same questions
    the audit view would.

    The four token fields follow the Anthropic SDK's
    ``Usage`` shape so the helper in ``agent.py`` can
    copy keys verbatim. The v0 UI only renders
    ``input_tokens`` + ``output_tokens``; cache fields are
    stored so a future dashboard view doesn't need a schema
    change to expose them.

    ``ts`` is naive UTC (matching the convention every other
    timestamp column in this file uses). The aggregation
    endpoint converts the configured timezone's
    ``period_start`` / ``period_end`` to UTC before issuing
    the SQL — see ``_period_bounds`` in
    ``employee_metrics.py``. Storing tz-aware would force a
    schema decision (which tz?) that the system-level
    setting handles better.

    ``employee_id`` is NOT NULL: every chat call in v0
    resolves to a concrete employee before reaching the
    LLM (WebUI cookie admin + TG bound employee), so the
    FK is always satisfied. If a future channel arrives
    without a ``chat_id`` → ``Employee`` mapping, the
    insert will surface that gap at write time rather
    than silently dropping the row.
    """
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    cache_creation_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Composite index — supports the aggregation endpoint's
    # ``WHERE employee_id = ? AND ts BETWEEN ? AND ?``.
    # Listed in ``__table_args__`` so it gets created
    # alongside the table by ``create_all`` on a fresh DB;
    # for an existing DB, ``_INDEX_MIGRATIONS`` below
    # patches it in via ``CREATE INDEX IF NOT EXISTS``.
    __table_args__ = (
        Index("ix_token_usage_emp_ts", "employee_id", "ts"),
    )

    # Read-only relationship for admin / debug views; route
    # code never traverses it to mutate the employee.
    employee: Mapped["Employee"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"TokenUsage(id={self.id}, employee_id={self.employee_id}, "
            f"in={self.input_tokens}, out={self.output_tokens}, "
            f"ts={self.ts.isoformat()})"
        )


# ────────────────────────────────────────────────────────────────── #
# Chat sessions — D.18: replaces the per-session JSON files under
# `<workspace>/memories/sessions/<chat_id>/<sid>.json` with two
# rows tables. ``chat_sessions`` is the header; ``chat_messages`` is
# the active + archived message log, where ``archived=0`` is the
# LLM-facing "active" view (compressed) and ``archived=1`` is the
# forensic archive (D.17's append-only log).
#
# The session row is keyed by the 26-char ULID ``session_id`` so
# callers that already hold an id (chat.py / bot.py / agent.py) can
# stay on their existing keys without translation.
# ────────────────────────────────────────────────────────────────── #


class ChatSession(Base):
    """A chat session header.

    The body of the session (messages + archive) lives in
    :class:`ChatMessage` and is loaded on demand by
    :class:`magi.runtime.sessions.SessionStore.get`.
    """

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    # ``tgid`` is the Telegram chat identifier. For WebUI
    # sessions the value is the admin's telegram_id (the
    # cookie); for TG inbound sessions it's the TG user's
    # chat_id. The column is specifically the **Telegram**
    # chat id — not a generic "chat_id" — because future IM
    # platforms (Slack, WeChat, etc.) will each have their
    # own identifier scheme and we don't want to overload one
    # column with three different semantics. When a non-TG
    # channel lands, the schema will gain a sibling column
    # (e.g. ``slack_chat_id``) or a generic
    # ``(platform, external_id)`` pair; the search scope
    # stays on ``employee_id`` either way.
    #
    # Indexed so the per-channel "list my conversations"
    # endpoint (``GET /api/chat/sessions``) is a single
    # range scan per ``tgid``.
    tgid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # The employee operator whose history this row belongs
    # to. This is the search-scope key (D.18+1): an admin
    # searching with the ``search_sessions`` tool sees
    # every session whose ``employee_id`` matches their
    # own — webui, TG, and (in future) any other channel,
    # unified. ``tgid`` is a per-channel row identifier;
    # ``employee_id`` is the cross-channel identity.
    employee_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # D.7 — operator-set or auto-titled. ``null`` until either
    # has run. Bounded to 80 chars at the Pydantic boundary.
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # D.17 — snapshot of ``system.compact_keep_recent`` at the
    # last compaction pass. Pure audit trail; the next
    # compaction reads the live setting.
    active_tail_count: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    # Indexed so ``list_summaries`` ``ORDER BY updated_at DESC`` is
    # a backward index walk per ``chat_id``.
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Read-only back-reference; routes never mutate via this
    # collection. ``cascade="all, delete-orphan"`` so a session
    # delete also clears its message rows.
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        viewonly=True,
        order_by="ChatMessage.id",
    )

    def __repr__(self) -> str:
        return (
            f"ChatSession(session_id={self.session_id}, "
            f"chat_id={self.chat_id}, title={self.title!r})"
        )


class ChatMessage(Base):
    """A single chat message, active or archived.

    Active rows (``archived=0``) are what the LLM sees in the next
    turn; archived rows (``archived=1``) are the pre-compaction
    history that ``_maybe_compact`` rolled out of ``active`` so
    the LLM's context window isn't blown by a long chat. Both are
    indexed by the same FTS5 virtual table (D.18 search) — search
    results span the whole conversation, not just the active tail.
    """

    __tablename__ = "chat_messages"

    # Autoincrement so the FTS5 ``rowid`` mapping is stable.
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Caller-supplied ULID (the same id used in D.6–D.17) so the
    # JSON-to-SQLite migration can carry the id across without
    # re-minting, and so chat_history rows can be deep-linked.
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # ISO UTC string (matches the JSON format); not DateTime so we
    # don't need tz-aware handling in code that already passes the
    # string through.
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    # 0 = active (LLM sees), 1 = archived (compressed out).
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        # Primary lookup pattern: "give me the active tail of
        # session X ordered by append-order".
        Index(
            "ix_chat_messages_session_archived",
            "session_id",
            "archived",
            "id",
        ),
        # Uniqueness on (session, message_id) lets the migration
        # importer do ``INSERT OR IGNORE`` without re-checking
        # for partial duplicates.
        UniqueConstraint(
            "session_id",
            "message_id",
            name="uq_chat_messages_session_msg",
        ),
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages", viewonly=True)

    def __repr__(self) -> str:
        flag = "A" if self.archived else "·"
        return (
            f"ChatMessage({flag} id={self.id} session={self.session_id} "
            f"role={self.role} ts={self.ts})"
        )


# -- bootstrap --------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _state_dir_from_env() -> str:
    return os.environ.get("MAGI_STATE_DIR", _DEFAULT_STATE_DIR)


def get_engine() -> Engine:
    """Return the process-wide engine, initialising it on first call.

    The engine is created lazily so importing this module is cheap
    (no DB connection until the first request). After the first
    call, every subsequent call returns the same engine.
    """
    global _engine, _SessionLocal
    if _engine is None:
        state_dir = Path(_state_dir_from_env())
        state_dir.mkdir(parents=True, exist_ok=True)
        db_path = state_dir / "magi.db"
        # ``check_same_thread=False`` lets the TG bot thread and
        # the FastAPI handler thread share the engine. SQLAlchemy
        # serialises access internally so this is safe.
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        _register_sqlite_pragmas(_engine)
        _register_begin_immediate(_engine)
        _SessionLocal = sessionmaker(
            bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False
        )
    return _engine


# -- SQLAlchemy connection event listeners ----------------------------------
# Two PRAGMAs and a transaction-mode override that the raw
# ``sqlite3`` connections in ``local_db.py`` / ``settings.py``
# already set on themselves, but which SQLAlchemy connections
# don't inherit (SQLAlchemy creates fresh DBAPI connections from
# the pool — each needs the PRAGMAs re-applied).
#
# ``busy_timeout`` makes contending writers (TG bot thread +
# FastAPI loop hitting the same row under D.18 search/append/
# compaction) wait up to 5 s instead of immediately raising
# ``database is locked``. ``foreign_keys=ON`` is opt-in per
# connection in SQLite (off by default for backwards compat).
#
# ``begin`` → IMMEDIATE replaces SQLAlchemy's default DEFERRED
# transactions. With WAL that means every write transaction
# takes the reserved lock at BEGIN instead of upgrading on the
# first write, eliminating the ``SQLITE_BUSY`` window where two
# writers could both think they were alone.


def _register_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()


def _register_begin_immediate(engine: Engine) -> None:
    @event.listens_for(engine, "begin")
    def _begin(dbapi_conn):
        # SQLAlchemy normally issues "BEGIN" (DEFERRED). Replace
        # with "BEGIN IMMEDIATE" so writes don't get a SQLITE_BUSY
        # at upgrade time under contention. Reads inside a
        # transaction still see a consistent snapshot.
        dbapi_conn.exec_driver_sql("BEGIN IMMEDIATE")


def init_orm(state_dir: str | None = None) -> Engine:
    """Create all ORM tables and return the engine.

    Idempotent — ``create_all`` is a no-op for tables that
    already exist, so calling on every boot is safe. Run from
    ``magi.node.run`` alongside ``init_sqlite``; the two target
    the same file but different tables, so they don't conflict.

    Also runs a tiny ``ALTER TABLE`` pass to add new columns to
    pre-existing tables — ``create_all`` only creates *missing*
    tables, not missing columns on existing ones, so this is
    needed as the schema grows within C1.x. The first Alembic
    baseline (planned for end of C1.3) replaces this.
    """
    engine = get_engine()
    if state_dir is not None:
        # Honour an explicit override (mostly for tests).
        os.environ["MAGI_STATE_DIR"] = state_dir
    # The proactive runtime (Task / TaskRun) ships in a
    # separate package; import it here so its tables
    # register on ``Base.metadata`` before ``create_all``
    # runs. Doing this inside ``init_orm`` (rather than
    # at module top) keeps the eager-import surface
    # tight — callers that never touch the proactive
    # module don't pay apscheduler's import cost
    # until something asks for a scheduled task.
    import magi.runtime.proactive.orm_models  # noqa: F401 — registers on Base
    Base.metadata.create_all(engine)
    _run_inline_migrations(engine)
    _seed_default_root(engine)
    logger.info(
        "orm initialised",
        extra={"tables": sorted(Base.metadata.tables.keys())},
    )
    return engine


# -- seed defaults (pre-Alembic) -------------------------------------------
#
# Hand-seeded "ensure the workspace has a root" bootstrap. The
# default name is hardcoded to "MAGI.org" — the deployer can
# rename it via the dashboard PATCH endpoint (it'll get
# re-created as MAGI.org on a fresh DB if they ever wipe and
# start over, but a renamed root stays renamed). When C8
# hardening lands this becomes configurable via env var.
_DEFAULT_ROOT_DEPT_NAME = "MAGI.org"


def _seed_default_root(engine: Engine) -> None:
    """Ensure the org tree has a root department.

    On first boot, if no departments exist at all, seed a
    single top-level ``MAGI.org`` row so the org tree always
    has an anchor. If the deployer later deletes it, this
    will recreate it on the next boot — which is the right
    trade-off for C0 (we don't have a "root" concept enforced
    by the schema yet; C3 / C6 will likely require every
    employee to belong to a non-root department anyway).
    """
    with Session(engine) as session:
        if session.scalar(select(Department.id).limit(1)) is not None:
            return
        session.add(
            Department(
                name=_DEFAULT_ROOT_DEPT_NAME,
                parent_id=None,
                manager_id=None,
            )
        )
        session.commit()
        logger.info(
            "seeded default root department: %s", _DEFAULT_ROOT_DEPT_NAME
        )


# -- inline migrations (pre-Alembic) ---------------------------------------
#
# SQLAlchemy's ``create_all`` is a no-op when the table already
# exists, so it can't add a new column to an existing table. For
# C1.1 we have a small list of known migrations to run by hand;
# the first Alembic baseline (end of C1.3) takes over from here.
#
# Each entry is ``(table, column, ddl_fragment)``. ``ddl_fragment``
# is the part after the column name, e.g. ``"INTEGER REFERENCES
# departments(id)"``. NULL is the default, so existing rows
# survive the add.
#
# Columns that need a UNIQUE constraint are listed separately
# in :data:`_UNIQUE_INDEX_MIGRATIONS` because SQLite refuses
# ``ALTER TABLE ... ADD COLUMN ... UNIQUE`` ("Cannot add a
# UNIQUE column") on pre-existing tables. The workaround is
# to add the column plain, then create the unique index.
_INLINE_MIGRATIONS: list[tuple[str, str, str]] = [
    # C1.1: added department_id, provider, api_key to employees.
    ("employees", "department_id", "INTEGER REFERENCES departments(id) ON DELETE SET NULL"),
    ("employees", "provider", "VARCHAR(32)"),
    ("employees", "api_key", "VARCHAR(512)"),
    # C1.1 (soft-delete): separated_at lets the dashboard mark
    # an employee as 离职 without losing the row.
    ("employees", "separated_at", "DATETIME"),
    # C1.x (role + TG binding): unifies the WebUI Access list
    # with the employees table. Existing rows default to
    # role='assigned' (in v0 single-instance, this MAGI
    # serves every employee); telegram_id stays NULL until
    # the /start binding flow runs. The UNIQUE constraint
    # on telegram_id is added as a separate index step
    # below (SQLite can't ALTER TABLE ADD COLUMN with
    # UNIQUE).
    ("employees", "role", "VARCHAR(16) NOT NULL DEFAULT 'assigned'"),
    ("employees", "telegram_id", "BIGINT"),
]

# Column renames. ``(table, old_name, new_name)``. The
# migration is a one-shot ``ALTER TABLE ... RENAME COLUMN``
# (SQLite 3.25+; CPython 3.12 ships 3.45+) executed the
# first time a database is opened with the new column name
# present and the old one absent. Re-runs on the same DB
# are no-ops. D.18+1 renamed ``chat_sessions.chat_id`` →
# ``chat_sessions.tgid`` so the column's purpose
# (Telegram chat identifier only, NOT a generic chat id)
# is reflected in its name; the WebUI/TG future-IM
# cross-platform scope now lives on ``employee_id``.
_RENAME_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        "chat_sessions",
        "chat_id",
        "tgid",
    ),
]

# Plain index pairs. ``(table, index_name, columns_ddl)``.
# Run after the plain ALTER TABLE above for read-side speed.
# Idempotent (``CREATE INDEX IF NOT EXISTS``).
_INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
    # Speeds up ``GET /api/action_items`` which always filters
    # by employee_id; the second index supports the
    # "open + last-7-days completed" listing ordered by recency.
    (
        "action_items",
        "ix_action_items_employee_id",
        "(employee_id)",
    ),
    (
        "action_items",
        "ix_action_items_employee_recent",
        "(employee_id, created_at DESC)",
    ),
    # D.15 — token-bill aggregation. ``create_all`` builds
    # this alongside the new ``token_usage`` table on fresh
    # installs; the ``CREATE INDEX IF NOT EXISTS`` here
    # covers existing DBs (the inline migration runner is
    # idempotent). The composite covers the
    # ``WHERE employee_id = ? AND ts BETWEEN ? AND ?`` query
    # the per-period endpoint issues.
    (
        "token_usage",
        "ix_token_usage_emp_ts",
        "(employee_id, ts)",
    ),
    # 定时 / 循环任务 (proactive runtime) — indexes
    # backfilled for existing DBs that pre-date the
    # proactive feature. The model declares the same
    # names in __table_args__; on fresh installs
    # ``create_all`` builds these alongside the new
    # tables. ``tasks(enabled, last_run_at)`` covers the
    # scheduler boot scan ("what's enabled and possibly
    # due?") and the operator's primary listing. The
    # ``task_runs`` composite covers the history pane's
    # primary access pattern: per task, ordered by
    # started_at desc.
    (
        "tasks",
        "ix_tasks_enabled_last_run",
        "(enabled, last_run_at)",
    ),
    (
        "tasks",
        "ix_tasks_employee",
        "(employee_id)",
    ),
    (
        "task_runs",
        "ix_task_runs_task_started",
        "(task_id, started_at)",
    ),
]

# Unique-index triples. ``(table, index_name, columns_ddl,
# where_clause_or_None)``. The where_clause is a partial-index
# predicate; ``None`` falls back to ``WHERE <last_column> IS
# NOT NULL`` (the original behaviour for the employees
# telegram_id index, which is nullable for non-bound rows).
_UNIQUE_INDEX_MIGRATIONS: list[tuple[str, str, str, str | None]] = [
    (
        "employees",
        "ux_employees_telegram_id",
        "telegram_id",
        None,
    ),
    # Action items: idempotency — one OPEN row per
    # ``(employee_id, kind)``. ``Partial unique`` so a
    # completed/dismissed row doesn't block a future same-kind
    # prompt (e.g. operator removes admin, re-adds them:
    # a future prompt of the same kind must be allow-listed).
    (
        "action_items",
        "ux_action_items_open_per_kind",
        "employee_id, kind",
        "completed_at IS NULL AND dismissed = 0",
    ),
]


# -- FTS5 virtual table (D.18 search) ----------------------------------------
#
# ``chat_messages_fts`` is an external-content FTS5 table that
# mirrors ``chat_messages.text``. Three triggers (ai / ad / au) keep
# it in sync with INSERT / DELETE / UPDATE on the source table.
#
# Tokenizer choice — ``trigram``:
#
#   - CJK: 3-character substring match. E.g. searching "压缩触发"
#     finds messages containing that 3-character run anywhere in
#     the text. Without trigram (with default unicode61), CJK runs
#     are a single token and only exact-prefix matches return.
#   - Latin / digits: same 3-char substring semantics; matches
#     "son" inside "Jefferson" etc. ``LIKE``-style behaviour
#     without the operator-vocabulary quirks of LIKE patterns.
#
# pysqlite3-binary wheels deliberately don't ship ICU, so the
# ``tokenize='icu'`` route that would give true CJK word
# segmentation requires a self-compiled SQLite + libicu link.
# Trigram is the "good enough for v0, no extra build cost" pick.
# Operators who type a single CJK character get a "use at least
# 3 characters" hint from the search UI; everything ≥3 chars
# just works.
#
# If FTS5 itself is missing from the linked SQLite (rare on
# CPython 3.12 builds, but possible on stripped-down distros),
# the CREATE TABLE DDL fails. We catch the failure, log a warning,
# and let ``chat_search`` route return 503 ``search.unavailable``.
# The ORM init does NOT abort, so a botched FTS install can't
# brick the whole node.

_FTS_MIGRATIONS: list[tuple[str, str]] = [
    # Virtual table. ``content='chat_messages'`` means the FTS5
    # index doesn't store a copy of the text — it pulls live
    # from the source row by rowid at query time. The downside
    # (slower snippet() reads) is irrelevant at v0 scale;
    # the upside (no double-storage, no drift) is huge.
    (
        "chat_messages_fts",
        "CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5("
        "    text, "
        "    content='chat_messages', "
        "    content_rowid='id', "
        "    tokenize='trigram'"
        ")",
    ),
    # Sync triggers. The standard 3-trigger external-content
    # pattern from SQLite's FTS5 docs.
    (
        "chat_messages_ai",
        "CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages BEGIN "
        "    INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text); "
        "END",
    ),
    (
        "chat_messages_ad",
        "CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN "
        "    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) "
        "        VALUES('delete', old.id, old.text); "
        "END",
    ),
    (
        "chat_messages_au",
        "CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN "
        "    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) "
        "        VALUES('delete', old.id, old.text); "
        "    INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text); "
        "END",
    ),
]


def _run_inline_migrations(engine: Engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        # Column renames first — once the column is renamed
        # to its new name, the ``CREATE TABLE`` of a fresh DB
        # that already declares the new column will see
        # ``table_info`` reflect it, and the migrations
        # below that key off ``table_info`` won't try to
        # re-create it.
        for table, old_name, new_name in _RENAME_COLUMN_MIGRATIONS:
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if new_name in existing:
                # Already migrated (or fresh DB).
                continue
            if old_name not in existing:
                # Fresh DB with the new schema — nothing to
                # rename (CREATE TABLE declared ``new_name``
                # directly).
                continue
            logger.info(
                "inline migration: renaming %s.%s → %s",
                table, old_name, new_name,
            )
            conn.execute(
                text(
                    f"ALTER TABLE {table} "
                    f"RENAME COLUMN {old_name} TO {new_name}"
                )
            )

        for table, column, ddl in _INLINE_MIGRATIONS:
            # PRAGMA table_info returns one row per column; the
            # second element is the column name.
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if column in existing:
                continue
            logger.info(
                "inline migration: adding %s.%s",
                table,
                column,
            )
            conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            )

        for table, index_name, columns in _INDEX_MIGRATIONS:
            logger.info(
                "inline migration: ensuring index %s on %s.%s",
                index_name, table, columns,
            )
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"{index_name} ON {table} {columns}"
                )
            )

        for table, index_name, columns, where_clause in _UNIQUE_INDEX_MIGRATIONS:
            logger.info(
                "inline migration: ensuring unique index %s on %s.%s",
                index_name, table, columns,
            )
            # ``where_clause`` is None → default to "WHERE
            # <last column> IS NOT NULL" (preserves the original
            # behaviour for ux_employees_telegram_id). For
            # partial indexes (``ux_action_items_open_per_kind``)
            # the caller supplies the actual predicate.
            if where_clause is None:
                last_col = columns.split(",")[-1].strip()
                predicate = f"WHERE {last_col} IS NOT NULL"
            else:
                predicate = f"WHERE {where_clause}"
            conn.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS "
                    f"{index_name} ON {table} ({columns}) "
                    f"{predicate}"
                )
            )

        # FTS5 virtual table + sync triggers. Probe the compile
        # options first; on a stripped SQLite (e.g. Alpine
        # musllinux without FTS5) we log and skip so ORM init
        # still succeeds — ``chat_search`` returns 503 in that
        # case instead of the whole node refusing to boot.
        try:
            has_fts5 = (
                conn.execute(
                    text(
                        "SELECT 1 FROM pragma_compile_options "
                        "WHERE compile_options = 'ENABLE_FTS5'"
                    )
                ).first()
                is not None
            )
        except Exception:
            has_fts5 = False
        if has_fts5:
            try:
                for name, ddl in _FTS_MIGRATIONS:
                    logger.info("fts migration: %s", name)
                    conn.execute(text(ddl))
                # External-content FTS indexes start empty —
                # populate from any existing chat_messages rows
                # so a botched restart / partial migration is
                # self-healing.
                conn.execute(
                    text(
                        "INSERT INTO chat_messages_fts(chat_messages_fts) "
                        "VALUES('rebuild')"
                    )
                )
            except Exception as e:
                # Some SQLite builds compile FTS5 but reject
                # ``tokenize='trigram'`` (rare). Treat that the
                # same as "no FTS5" and keep the ORM init alive.
                logger.warning(
                    "fts migration failed (%s); search route will return 503",
                    e,
                )
        else:
            logger.warning(
                "FTS5 not compiled into this SQLite; "
                "chat search will return 503"
            )


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yield a session, close on request end.

    Usage in a route::

        from fastapi import Depends
        from magi.runtime.state.orm import get_session

        @router.get(...)
        def list_departments(session: Session = Depends(get_session)):
            ...
    """
    if _SessionLocal is None:
        # The first request arrives before ``init_orm`` was called
        # (shouldn't happen in production — boot always runs init
        # first — but be defensive). Initialise on demand.
        init_orm()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def open_session() -> Generator[Session, None, None]:
    """Context-manager variant of :func:`get_session`.

    Use this from code that needs a session outside the
    FastAPI request lifecycle — the TG bot thread, the
    background scheduler, the workspace bootstrap, etc.
    Inside FastAPI route handlers prefer
    ``Depends(get_session)`` so the session closes at the
    same point the response is sent.
    """
    if _SessionLocal is None:
        init_orm()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
