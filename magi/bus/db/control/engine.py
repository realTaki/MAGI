"""Local control-plane SQLite engine factory.

Mirrors :mod:`magi.bus.db.magis.local_engine` — same WAL /
``busy_timeout`` / ``foreign_keys`` / ``synchronous=NORMAL`` / ``BEGIN
IMMEDIATE`` policy, but rooted at ``<data_root>/control/local-registry.db``
instead of a per-MAGIS file.  Phase 4 reads from this engine when
the launcher wants to reconcile stale PIDs / orphaned workspaces
across restarts.

Per plan §6.2 the engine is selected by the Composition Root and
injected; business modules never call ``build_control_engine()``
directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

logger = logging.getLogger("magi.bus.db.control.engine")


def build_control_engine(control_dir: Path) -> Engine:
    """Build a SQLite engine rooted at ``<control_dir>/local-registry.db``.

    The directory is created if missing.  Migrations run on first
    construction via :func:`_run_alembic_upgrade`.
    """
    control_dir = Path(control_dir).resolve()
    control_dir.mkdir(parents=True, exist_ok=True)
    db_path = control_dir / "local-registry.db"

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(dbapi_conn):
        dbapi_conn.exec_driver_sql("BEGIN IMMEDIATE")

    _run_alembic_upgrade(engine)

    logger.info(
        "local control engine built",
        extra={"control_dir": str(control_dir), "db_path": str(db_path)},
    )
    return engine


def _run_alembic_upgrade(engine: Engine) -> None:
    """Run the control-registry Alembic migrations.

    The control alembic env is intentionally separate from the
    per-MAGIS alembic env so the K8s ``MAGIS_POSTGRES_URL`` Alembic
    doesn't try to read the local-registry SQLite.  When no revision
    directory exists yet (the very first launch), ``Base.metadata.create_all``
    establishes the baseline so the launcher can boot before a
    migration is written.
    """
    from magi.bus.db.control.models import Base

    Base.metadata.create_all(engine)


__all__ = ["build_control_engine"]
