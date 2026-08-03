"""Bus persistence layer — engines, sessions, ORM base, KV, migrations.

This subpackage is **internal to the bus**.  The leading underscore is the
Python convention; the *enforcement* is the AST import-boundary test
(``tests/architecture/test_import_boundaries.py``) which forbids
``magi.agent``, ``magi.tools``, ``magi.channels``, ``magi.proactive``,
``magi.connectors``, ``magi.orchestrator``, ``magi.mcp``, and ``magi.skills``
from importing anything under ``magi.bus._persistence``.

External callers (agent, tools, channels, mcp, proactive, orchestrator,
skills, etc.) must use the bus service façades
(``bus.session``, ``bus.memory``, ``bus.contacts``, ``bus.magis``,
``bus.magic``, ``bus.tool_catalog``, ...).  Bus services are the
**only** path between domain code and the storage layer.

The lone allowed non-bus callers of ``_persistence`` are the composition
root (``magi.__main__``) and the Alembic migration runner — they own the
engine + metadata at process startup, before any service exists.

ORM models are NOT re-exported here.  Bus services import them from
their canonical homes under :mod:`magi.bus.models.{local,magis,queue}`
(those modules declare ``from magi.bus._persistence.base import Base``
— no cycle).  Keeping ORM tables out of ``_persistence.__init__`` avoids
the import-time cycle that would otherwise form
(``_persistence.__init__`` → ``models.queue.a2a_invocation`` →
``_persistence.base`` → ``_persistence.__init__``).

Public surface (for bus services + the composition root + Alembic)
-------------------------------------------------------------------

Engine / session / base / KV (private engines, public API for the
composition root and Alembic only):

- ``Base``, ``utcnow_naive`` — declarative base + naive UTC helper
- ``init_orm``, ``init_sqlite``, ``open_session``, ``get_engine``,
  ``get_session``, ``require_state_dir`` — local SQLite lifecycle
- ``state_get``, ``state_set``, ``state_delete`` — local SQLite KV
- ``get_magis_engine``, ``get_magis_session``, ``init_magis_public_db``,
  ``open_magis_session`` — MAGIS PostgreSQL access

ORM tables are imported from ``magi.bus.models.{local,magis,queue}``
directly — the bus services already use these paths.
"""

from magi.bus._persistence.base import Base, utcnow_naive
from magi.bus._persistence.engine import (
    get_engine,
    get_session,
    init_orm,
    open_session,
    require_state_dir,
)
from magi.bus._persistence.local_db import init_sqlite
from magi.bus._persistence.settings import (
    state_delete,
    state_get,
    state_set,
)

# MAGIS PostgreSQL access
from magi.bus._persistence.magis import (
    get_magis_engine,
    get_magis_session,
    init_magis_public_db,
    open_magis_session,
)


__all__ = [
    # base + engine
    "Base",
    "utcnow_naive",
    "get_engine",
    "get_session",
    "init_orm",
    "init_sqlite",
    "open_session",
    "require_state_dir",
    # local SQLite KV
    "state_get",
    "state_set",
    "state_delete",
    # MAGIS PG
    "get_magis_engine",
    "get_magis_session",
    "init_magis_public_db",
    "open_magis_session",
]

