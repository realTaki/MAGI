"""Local MAGIS SQLite engine factory.

Phase 3 — used by the Local Profile to back one MAGIS's public schema
with a per-MAGIS SQLite file (``<magis_dir>/magis.db``).  Distinct
from the Adam's private ``magi.db``; per plan §6.1 the legacy
fallback to the private database is forbidden for Local Profile
deployments.

The engine carries the same SQLite policy the rest of the BUS uses
(``WAL``, ``busy_timeout``, ``foreign_keys``, ``synchronous=NORMAL``)
plus Alembic ``upgrade head`` so the schema matches the K8s Profile
exactly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

logger = logging.getLogger("magi.bus.db.magis.local_engine")


def build(magis_dir: Path) -> Engine:
    """Construct a SQLAlchemy engine rooted at ``<magis_dir>/magis.db``.

    The directory is created if missing.  Alembic migrations run
    in-place via the per-MAGIS alembic env
    (:mod:`magi.bus.db.magis.alembic.env`).
    """
    magis_dir = Path(magis_dir).resolve()
    magis_dir.mkdir(parents=True, exist_ok=True)
    db_path = magis_dir / "magis.db"
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

    logger.info(
        "local MAGIS engine built",
        extra={"magis_dir": str(magis_dir), "db_path": str(db_path)},
    )
    return engine


__all__ = ["build"]