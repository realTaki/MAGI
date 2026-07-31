"""MAGI persistence boundaries.

Holds the SQLAlchemy ORM models plus private and public database access. The
package used to be called ``state`` and live
at :mod:`magi.agent.state`; the rename + split is the
current shape, the old module path is gone (callers
were updated in lockstep).

Layout:

  - :mod:`.base`                — :class:`Base` declarative class
  - :mod:`.engine`              — private per-MAGI SQLite engine +
                                  ``init_orm`` / ``get_session`` /
                                  ``open_session``
  - :mod:`.magis`               — public PostgreSQL engine and sessions for
                                  the MAGI's one direct MAGIS; organisation
                                  routes and runtime configuration use this
                                  boundary
  - :mod:`.models_contact`      — :class:`Contact` (the unified
                                  person directory; replaces the old
                                  ``contacts``, ``contact_entries``,
                                  and ``user_im_bindings`` tables)
  - :mod:`.models_magis`        — :class:`MAGIS` (MAGI Societies, a tree)
  - :mod:`.models_magic`        — :class:`MAGIC` (individual MAGI rows)
  - :mod:`.models_mcp_server`   — :class:`McpServer` (operator-
                                  configured MCP server rows; the
                                  loader reads these in place of
                                  the legacy ``mcp.json`` file)
  - :mod:`.models_dashboard`    — :class:`ActionItem`, :class:`TokenUsage`
  - :mod:`.alembic`            — versioned schema migrations
  - :mod:`.alembic_runner`     — startup ``upgrade head`` integration
  - :mod:`.migrations`         — legacy pre-Alembic adoption pass only
  - :mod:`.local_db`            — raw-SQL ``meta`` KV table
                                  (kept hand-rolled, see module docstring)
  - :mod:`.models_setting`      — :class:`Setting` (the system-level
                                  settings KV table)
  - :mod:`.settings`            — compatibility facade for the legacy
                                  ``state_get`` / ``state_set`` API

The models are shared declarations because the public MAGIS schema has foreign
keys across them.  In production, only the narrow organisation subset is
created through :mod:`.magis`; private models and tables remain in SQLite.

Public surface (re-exported below): the names the ~30
external callers need (``Base`` + every model class +
the engine helpers). New code can import from the
focused submodules; the facade is here for back-compat
in routes + tests.

The session-domain tables (:class:`ChatSession`,
:class:`ChatMessage`) live in
:mod:`magi.agent.memory.session.tables` — they're owned by
the ``session`` package (singular: this is the *manager*
of sessions, not a place where sessions are *stored* in
bulk). The db package re-exports them so existing
``from magi.agent.db import ChatSession`` imports keep
working.
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
from magi.agent.db.models_action_item import ActionItem
from magi.agent.db.models_contact import Contact, ContactNote
from magi.agent.db.models_eve_runtime import EveRuntime
# Naming refresh (2026-07): ``MAGIS`` = a MAGI Society (a group
# of MAGIs; lives in the ``magis`` table).
# ``MAGIC`` = an individual MAGI agent (internal table/API name;
# lives in the ``magic`` table).
from magi.agent.db.models_magic import MAGIC
from magi.agent.db.models_magis import MAGIS
from magi.agent.db.models_magis_membership import MAGISMembership, MAGISRole
from magi.agent.db.models_mcp_server import McpServer
from magi.agent.db.models_setting import Setting
from magi.agent.db.models_token_usage import TokenUsage

# Session-domain tables — owned by ``magi.agent.session``
# but re-exported here for callers that want a single import
# surface (``from magi.agent.db import ChatSession``).
from magi.agent.memory.session.tables import ChatMessage, ChatSession

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
    "Contact",
    "ContactNote",
    "MAGIC",
    "MAGIS",
    "MAGISMembership",
    "MAGISRole",
    "EveRuntime",
    "McpServer",
    # dashboard
    "ActionItem",
    "TokenUsage",
    "Setting",
    # sessions (re-exported from sessions/tables.py)
    "ChatSession",
    "ChatMessage",
]
