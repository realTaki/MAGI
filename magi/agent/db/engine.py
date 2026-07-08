"""MAGI ``db`` package — engine, bootstrap, and session helpers.

Lives in the same SQLite file as the hand-rolled ``settings`` KV
store (C0). The two coexist: ``settings`` and ``meta`` are written
by the raw-SQL helpers in :mod:`magi.agent.db.local_db` /
:mod:`magi.agent.db.settings`; everything else uses SQLAlchemy via
this module.

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
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from magi.agent.db.base import Base
from magi.agent.db.migrations import _run_inline_migrations


logger = logging.getLogger("magi.agent.db.engine")


class _MissingStateDirError(RuntimeError):
    """``MAGI_STATE_DIR`` is unset.

    Raised by :func:`require_state_dir` when no env var is
    present. The previous behaviour silently fell back to
    ``/workspace/memories``, which leaked a host-system path
    on dev machines (where ``/workspace`` is not a real
    mounted volume) and produced a confusing ``magi.db``
    on the host filesystem.
    """


def require_state_dir() -> str:
    """Return the absolute path to the state directory.

    Reads ``MAGI_STATE_DIR``. If the env var is missing, raises
    :class:`_MissingStateDirError` with a clear remediation
    message — explicitly named so the dev sees what's wrong
    instead of a cryptic ``sqlite3.OperationalError`` on a
    phantom path.

    Production (Docker) sets the env var in the image
    (``deploy/Dockerfile`` / ``Dockerfile.dev``). Tests set
    it via ``monkeypatch.setenv``. CLI invocations without
    the env var get the explicit error.
    """
    sd = os.environ.get("MAGI_STATE_DIR")
    if not sd:
        raise _MissingStateDirError(
            "MAGI_STATE_DIR is not set. Production sets it in "
            "deploy/Dockerfile (=/workspace/memories). For local "
            "dev, export MAGI_STATE_DIR=./.state or similar before "
            "running. Tests must monkeypatch.setenv it per test."
        )
    return sd


# -- bootstrap --------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _state_dir_from_env() -> str:
    return require_state_dir()


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
    # Local import — Department is defined in models_org which
    # depends on ``Base`` already being constructed (a forward
    # import here would break the package init order).
    from magi.agent.db.models_org import Department

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
    # Eagerly import every model module so its tables register
    # on ``Base.metadata`` before ``create_all`` runs. Doing
    # this inside ``init_orm`` (rather than at module top) keeps
    # the eager-import surface tight — callers that never touch
    # a given module don't pay its import cost until something
    # asks for a row from that table.
    import magi.agent.db.models_org  # noqa: F401 — registers on Base
    import magi.agent.db.models_dashboard  # noqa: F401
    import magi.agent.sessions.tables  # noqa: F401 — sessions-owned tables
    import magi.agent.proactive.orm_models  # noqa: F401 — proactive runtime
    Base.metadata.create_all(engine)
    _run_inline_migrations(engine)
    _seed_default_root(engine)
    logger.info(
        "orm initialised",
        extra={"tables": sorted(Base.metadata.tables.keys())},
    )
    return engine


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yield a session, close on request end.

    Usage in a route::

        from fastapi import Depends
        from magi.agent.db.engine import get_session

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