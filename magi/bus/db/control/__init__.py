"""Local Profile control-plane registry.

Phase 3 close-out — the SQLite-backed ``local-registry.db`` lives here.
K8s Profile does not write to this database (it reads/writes the
``eve_runtime`` table in the central PostgreSQL MAGIS instead); the
Local Profile keeps everything the launcher, supervisor, and
``LocalProcessRuntimeBackend`` need to find an existing Runtime on
disk after a launcher restart.

Layering:

- ``engine``    — SQLAlchemy engine + ``Alembic`` runner
- ``models``    — ORM tables (``ControlRuntimeState``,
                  ``ControlPortAllocation``, ``ControlWorkspaceArchive``,
                  ``ControlSecret``)
- ``repository`` — Bus-facing command/query API; domain modules never
                   reach ``models`` or ``engine`` directly
- ``magi.bus.services.control_registry`` — the BUS facade wired into
                   ``magi.bus.Bus``

Per plan §6.1, ``LocalProcessRuntimeBackend`` writes here only via the
Orchestrator Worker (a Phase 4 component); it never opens the engine
or ORM session itself.
"""

from magi.bus.db.control.engine import build_control_engine
from magi.bus.db.control.repository import ControlRepository

__all__ = ["build_control_engine", "ControlRepository"]
