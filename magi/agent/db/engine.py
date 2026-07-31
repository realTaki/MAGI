"""MAGI ``db`` package — engine, bootstrap, and session helpers.

Lives in the same SQLite file as the small ``meta`` bootstrap table.
All application tables, including the legacy ``settings`` KV table, are
accessed through SQLAlchemy via this module. ``meta`` remains a raw-SQL
bootstrap table because it only carries schema hand-off metadata.

Schema ownership is now Alembic. ``init_orm`` runs ``alembic upgrade head``
before opening application sessions. The old inline migration module is used
only once to adopt pre-Alembic databases; versioned revisions own every
schema change after the baseline.

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
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from magi.agent.db.alembic_runner import stamp_baseline, upgrade_head
from magi.agent.db.base import Base
from magi.agent.db.migrations import _run_inline_migrations

logger = logging.getLogger("magi.agent.db.engine")


def require_state_dir() -> str:
    """Return the absolute path to the state directory.

    Returns the hardcoded :data:`magi.constants.STATE_DIR`
    (``/workspace/memories``).  The ``MAGI_STATE_DIR`` env
    var is honoured as an **escape hatch for tests only** —
    production code never sets or reads it.
    """
    from magi.constants import STATE_DIR
    return os.environ.get("MAGI_STATE_DIR") or STATE_DIR


# -- bootstrap --------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_aux_sessionmakers: dict[Path, sessionmaker[Session]] = {}


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
        _engine = _create_sqlite_engine(db_path)
        _SessionLocal = _make_sessionmaker(_engine)
    return _engine


def _create_sqlite_engine(db_path: Path) -> Engine:
    """Create an engine with the process-wide SQLite policy."""
    # ``check_same_thread=False`` lets the TG bot thread and the
    # FastAPI handler thread share the engine. SQLAlchemy
    # serialises access internally so this is safe.
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    _register_sqlite_pragmas(engine)
    _register_begin_immediate(engine)
    return engine


def _make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )


def _sessionmaker_for_explicit_state_dir(state_dir: Path) -> sessionmaker[Session]:
    """Return an ORM session factory for a legacy explicit state path.

    Normal application code uses the process-wide engine. The old KV helper
    API accepts ``state_dir`` directly, though, and a few migration-era
    callers can invoke it before the process environment is configured. Keep
    that compatibility without falling back to raw sqlite3: an explicit
    alternate path gets its own SQLAlchemy engine with the exact same SQLite
    transaction policy.
    """
    cached = _aux_sessionmakers.get(state_dir)
    if cached is not None:
        return cached

    state_dir.mkdir(parents=True, exist_ok=True)
    engine = _create_sqlite_engine(state_dir / "magi.db")
    # ``models_setting`` is imported by the compatibility facade before this
    # path is reached. Creating the registered metadata makes state_set work
    # even when only init_sqlite (the legacy meta bootstrap) ran first.
    Base.metadata.create_all(engine)
    factory = _make_sessionmaker(engine)
    _aux_sessionmakers[state_dir] = factory
    return factory


# -- SQLAlchemy connection event listeners ----------------------------------
# Two PRAGMAs and a transaction-mode override that the raw
# The raw ``meta`` bootstrap in ``local_db.py`` sets these for its own
# connection, but SQLAlchemy connections need the same policy registered
# here (each pooled DBAPI connection is fresh).
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
            # WAL mode = readers don't block writers, writers
            # don't block readers. Without WAL, an ``INSERT``
            # in the bot thread blocks every FastAPI
            # ``SELECT`` (and vice-versa); with WAL the
            # background scheduler's ``register()`` no
            # longer starves the test's serial
            # ``open_session()`` chain. Setting WAL on
            # every new connection is a no-op when the DB
            # is already in WAL mode; safe to call
            # repeatedly.
            cur.execute("PRAGMA journal_mode=WAL")
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


# -- seed defaults ----------------------------------------------------------
#
# Hand-seeded "ensure the workspace has a root" bootstrap. The
# default name is hardcoded to "Genesis" — the deployer can
# rename it via the dashboard PATCH endpoint (it'll get
# re-created as Genesis on a fresh DB if they ever wipe and
# start over, but a renamed root stays renamed). When C8
# hardening lands this becomes configurable via env var.
_DEFAULT_ROOT_MAGIC_NAME = "Genesis"


def _seed_default_root(engine: Engine) -> None:
    """Ensure the workspace has exactly one root MAGIS + a default adam MAGIC.

    The *root* of the society tree is identified by ``parent_id IS NULL``
    (not by the literal name ``"Genesis"``). On first boot, if no root
    row exists, seed one named ``_DEFAULT_ROOT_MAGIC_NAME`` ("Genesis") as
    the society anchor. If the deployer later renames or deletes the root,
    the next boot seeds a fresh Genesis only when no root row remains —
    so a renamed root stays renamed and we never accumulate duplicate
    roots. Also seeds one ``MAGIC`` row with ``magic_position='adam'``
    (named "Alice") for the root MAGIS so the "智能体管理" page is never
    empty on first boot.
    """
    # Local imports — the model modules depend on ``Base`` being
    # already constructed (a forward import here would break the
    # package init order).
    from magi.agent.db.models_magic import MAGIC
    from magi.agent.db.models_magis import MAGIS

    with Session(engine) as session:
        # Identity of "the root" is being the tree root (parent_id IS
        # NULL), NOT by the literal name "Genesis". Keying on the name
        # let a rename/delete of the root spawn a duplicate "Genesis"
        # on the next boot. Pick the first root row if several exist
        # (defensive) and only seed a fresh Genesis when no root row
        # exists at all.
        existing_root = session.scalar(
            select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id).limit(1)
        )
        if existing_root is None:
            root = MAGIS(
                name=_DEFAULT_ROOT_MAGIC_NAME,
                parent_id=None,
                adam_id=None,
            )
            session.add(root)
            session.flush()  # populate root.id
            logger.info(
                "seeded default root MAGIS: %s (id=%d)",
                _DEFAULT_ROOT_MAGIC_NAME, root.id,
            )
            existing_root = root

        # Seed one adam MAGIC under the root if none exists yet.
        has_adam = session.scalar(
            select(MAGIC.id).where(MAGIC.magic_position == "adam").limit(1)
        )
        if has_adam is None:
            adam = MAGIC(
                name="Alice",
                magis_id=existing_root.id,
                magic_position="adam",
                provider=None,
                api_key=None,
            )
            session.add(adam)
            session.flush()
            # Bind the Adam to the MAGIS row so the MagisPane
            # renders the ADAM column correctly.
            existing_root.adam_id = adam.id
            logger.info(
                "seeded default adam MAGIC under %s (id=%d)",
                _DEFAULT_ROOT_MAGIC_NAME, adam.id,
            )

        session.commit()


def init_orm(state_dir: str | None = None, *, seed_root: bool = True) -> Engine:
    """Run versioned migrations and return the process-wide engine.

    A database without ``alembic_version`` is a legacy C0/C1 database. For
    that one-time adoption path, existing ORM tables are first repaired by
    the old idempotent inline runner, then Alembic records the baseline. A
    fresh database skips the inline pass and is created by revision 0001.
    Once ``alembic_version`` exists, only Alembic revisions run.

    The engine is a process-global singleton keyed on the absolute
    path of the requested ``state_dir``. When ``state_dir`` changes
    (every test fixture uses a fresh tmp_path), the cached engine
    is disposed and rebuilt so the new fixture sees its own DB,
    not stale rows from a previous test that survived via the
    engine cache.
    """
    global _engine, _SessionLocal
    if state_dir is not None:
        # Honour an explicit override (mostly for tests) before creating
        # the lazy engine. This also lets the settings facade initialise a
        # fresh database from its legacy ``state_dir`` argument.
        os.environ["MAGI_STATE_DIR"] = state_dir
    requested_path = Path(_state_dir_from_env()).resolve()
    if _engine is not None:
        # If the cached engine points at a different directory,
        # dispose it so the next ``get_engine()`` rebuilds. The
        # cache check is by path (not by env var) so a re-entrant
        # test that reuses ``MAGI_STATE_DIR`` hits the fast path.
        try:
            bound_url = _engine.url.database
        except Exception:
            bound_url = None
        cached_path = Path(bound_url).resolve() if bound_url else None
        if cached_path != requested_path / "magi.db":
            try:
                _engine.dispose()
            except Exception:
                pass
            _engine = None
            _SessionLocal = None
            _aux_sessionmakers.clear()
    engine = get_engine()
    # Eagerly import every model module so its tables register
    # on ``Base.metadata`` before the migration runner starts. Doing
    # this inside ``init_orm`` (rather than at module top) keeps
    # the eager-import surface tight — callers that never touch
    # a given module don't pay its import cost until something
    # asks for a row from that table.
    import magi.agent.db.models_action_item  # noqa: F401
    import magi.agent.db.models_contact  # noqa: F401 — unified contact directory
    import magi.agent.db.models_eve_runtime  # noqa: F401 — EVE lifecycle state
    import magi.agent.db.models_magic  # noqa: F401 — MAGIC Citizen rows
    import magi.agent.db.models_magis  # noqa: F401 — MAGIS tree
    import magi.agent.db.models_mcp_server  # noqa: F401 — operator-configured MCP servers
    import magi.agent.db.models_setting  # noqa: F401 — legacy settings KV model
    import magi.agent.db.models_token_usage  # noqa: F401
    import magi.agent.memory.self.models  # noqa: F401 — self-memory table
    import magi.agent.memory.session.tables  # noqa: F401 — sessions-owned tables
    import magi.agent.proactive.orm_models  # noqa: F401 — proactive runtime
    application_tables = set(Base.metadata.tables)
    existing_tables = set(inspect(engine).get_table_names())
    is_legacy = "alembic_version" not in existing_tables
    has_legacy_application_tables = bool(application_tables & existing_tables)

    if is_legacy and has_legacy_application_tables:
        # Compatibility only: this path is for databases created before
        # Alembic did not exist yet. Repair legacy schemas before adoption;
        # this compatibility path must not receive new schema changes.
        Base.metadata.create_all(engine)
        _run_inline_migrations(engine)
        # The explicit baseline migration is for fresh databases. Legacy
        # databases already have those tables, so stamp rather than replay
        # CREATE TABLE statements, then let later revisions run normally.
        stamp_baseline(state_dir or require_state_dir())

    upgrade_head(state_dir or require_state_dir(), engine)
    # Built-in scheduled-task templates are code-owned prompts, not SQL
    # literals in a migration.  Synchronise them after schema upgrade so a
    # developer can tune a mounted YAML file and see it on the next boot.
    from magi.agent.proactive.preset_templates import sync_bundled_task_presets
    with Session(engine) as session:
        sync_bundled_task_presets(session)
        session.commit()
    if seed_root:
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
        def list_magis(session: Session = Depends(get_session)):
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
def open_session(
    state_dir: str | os.PathLike[str] | None = None,
) -> Generator[Session, None, None]:
    """Context-manager variant of :func:`get_session`.

    ``state_dir`` is optional and exists for compatibility with older
    helpers that accepted an explicit state directory. When it matches the
    process-wide path, the normal singleton is used. When it differs, an
    auxiliary SQLAlchemy engine is used for that explicit legacy path; this
    avoids silently switching the process-wide engine while keeping the
    settings facade on the ORM transaction policy.

    Use this from code that needs a session outside the
    FastAPI request lifecycle — the TG bot thread, the
    background scheduler, the workspace bootstrap, etc.
    Inside FastAPI route handlers prefer
    ``Depends(get_session)`` so the session closes at the
    same point the response is sent.
    """
    if state_dir is not None:
        requested = Path(state_dir).resolve()
        configured = os.environ.get("MAGI_STATE_DIR")
        if configured is None:
            os.environ["MAGI_STATE_DIR"] = str(requested)
            if _SessionLocal is None:
                init_orm()
            assert _SessionLocal is not None
            session_factory = _SessionLocal
        elif Path(configured).resolve() == requested:
            if _SessionLocal is None:
                init_orm()
            assert _SessionLocal is not None
            session_factory = _SessionLocal
        else:
            session_factory = _sessionmaker_for_explicit_state_dir(requested)
    else:
        if _SessionLocal is None:
            init_orm()
        assert _SessionLocal is not None
        session_factory = _SessionLocal

    session = session_factory()
    try:
        yield session
    finally:
        session.close()
