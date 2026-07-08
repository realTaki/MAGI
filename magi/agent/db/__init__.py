"""MAGI ``db`` package — SQLite persistence.

Holds every SQLAlchemy ORM table + the engine/session
machinery. The package used to be called ``state`` and live
at :mod:`magi.agent.state`; the rename + split is the
current shape, the old module path is gone (callers
were updated in lockstep).

Layout:

  - :mod:`.base`             — :class:`Base` declarative class
  - :mod:`.engine`           — engine singleton + ``init_orm``
                               + ``get_session`` / ``open_session``
  - :mod:`.models_org`       — :class:`Employee`, :class:`Department`
  - :mod:`.models_dashboard` — :class:`ActionItem`, :class:`TokenUsage`
  - :mod:`.migrations`       — pre-Alembic ``ALTER TABLE`` pass
                               + FTS5 sync triggers
  - :mod:`.local_db`         — raw-SQL ``meta`` KV table
                               (kept hand-rolled, see module docstring)
  - :mod:`.settings`         — raw-SQL ``settings`` KV table
                               (the C0 system-level config)

Public surface (re-exported below): the names the ~30
external callers need (``Base`` + every model class +
the engine helpers). New code can import from the
focused submodules; the facade is here for back-compat
in routes + tests.

The session-domain tables (:class:`ChatSession`,
:class:`ChatMessage`) live in
:mod:`magi.agent.sessions.tables` — they're owned by
the sessions package, not the db package. The db
package re-exports them so existing ``from
magi.agent.db import ChatSession`` imports keep working.
"""

from __future__ import annotations


# Re-export the public surface. Submodules below; the names
# here are what the ~30 external callers import.
from magi.agent.db.base import Base
from magi.agent.db.engine import (
    get_engine,
    get_session,
    init_orm,
    open_session,
    require_state_dir,
)
from magi.agent.db.local_db import init_sqlite
from magi.agent.db.models_dashboard import ActionItem, TokenUsage
from magi.agent.db.models_org import Department, Employee

# Session-domain tables — owned by ``magi.agent.sessions``
# but re-exported here for callers that want a single import
# surface (``from magi.agent.db import ChatSession``).
from magi.agent.sessions.tables import ChatMessage, ChatSession


__all__ = [
    # base + engine
    "Base",
    "get_engine",
    "get_session",
    "init_orm",
    "open_session",
    "require_state_dir",
    "init_sqlite",
    # org
    "Employee",
    "Department",
    # dashboard
    "ActionItem",
    "TokenUsage",
    # sessions (re-exported from sessions/tables.py)
    "ChatSession",
    "ChatMessage",
]