"""Public-database access for a MAGIS.

Each MAGI keeps a private SQLite database for its memory, sessions and local
work.  Organisation facts live in the PostgreSQL database of its direct
MAGIS.  The sole runtime discovery input is ``MAGIS_DATABASE_URL``; Kubernetes
mounts it from the MAGIS database Secret, rather than passing provider or
instruction values as environment variables.

This module is **internal to the bus**.  External callers (agent, tools,
channels, orchestrator, etc.) must not import from ``magi.bus._persistence``
directly — use the bus service façades
(``bus.magis``, ``bus.magic``, ``bus.contacts``, ...).  The lone
exceptions are the composition root (``magi.__main__``) and the Alembic
migration runner, which legitimately own the engine + metadata at startup.
"""

from magi.bus._persistence.magis.engine import (
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
