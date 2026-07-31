"""MAGIS public-database access.

This subpackage owns the engine and sessions for the one public PostgreSQL
database assigned to a MAGI's direct MAGIS.  Private, per-MAGI SQLite access
remains in :mod:`magi.agent.db.engine`.
"""

from magi.agent.db.magis.engine import (
    get_magis_engine,
    get_magis_session,
    init_magis_public_db,
    open_magis_session,
)

__all__ = [
    "get_magis_engine",
    "get_magis_session",
    "init_magis_public_db",
    "open_magis_session",
]
