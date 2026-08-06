"""Control-plane runtime registry — stored in the MAGIS database.

The repository operates on the MAGIS engine; models live in
:mod:`magi.bus.db.models.local.control_runtime`.  No separate SQLite file
is needed — the runtime registry, port allocations, workspace archives,
and control secrets are all co-located with organisation facts in
``MAGI_Societies/<id>-<slug>/magis.db`` (Local) or the MAGIS PostgreSQL (K8s).
"""

from magi.bus.db.repositories.magis.control import ControlRepository

__all__ = ["ControlRepository"]
