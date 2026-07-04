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
    create_engine,
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
    Permanent: unlike ``audit_log`` (a meta-key JSON blob
    capped at 1000 rows), this table is meant to grow
    indefinitely so week/month aggregates stay accurate.

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
        _SessionLocal = sessionmaker(
            bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False
        )
    return _engine


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


def _run_inline_migrations(engine: Engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
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
