"""Shared MAGI persistence boundaries.

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
``from magi.db import ChatSession`` imports keep
working.
"""

from __future__ import annotations

# Re-export the public surface. Submodules below; the names
# here are what the ~30 external callers import.
from magi.db.base import Base
from magi.db.engine import (
    get_engine,
    get_session,
    init_orm,
    open_session,
    require_state_dir,
)
from magi.db.local_db import init_sqlite
from magi.db.models_action_item import ActionItem
from magi.db.models_contact import Contact, ContactNote
from magi.db.models_control_plane import ControlOperator, ControlSetting
from magi.db.models_eve_runtime import EveRuntime
# Naming refresh (2026-07): ``MAGIS`` = a MAGI Society (a group
# of MAGIs; lives in the ``magis`` table).
# ``MAGIC`` = an individual MAGI agent (internal table/API name;
# lives in the ``magic`` table).
from magi.db.models_magic import MAGIC
from magi.db.models_magis import MAGIS
from magi.db.models_magis_admin import MAGISAdmin
from magi.db.models_magis_membership import MAGISMembership, MAGISRole
from magi.db.models_mcp_server import McpServer
from magi.db.models_setting import Setting
from magi.db.models_token_usage import TokenUsage
from magi.db.models_tool import ToolCatalogState, ToolDefinitionRecord, ToolRegistry

# The session/memory packages still accept historical ``magi.db.models_*``
# imports while they are migrated. Alias only modules already loaded above, so
# this does not create a second SQLAlchemy declarative registry.
import sys as _sys
_sys.modules["magi.db.engine"] = _sys.modules["magi.db.engine"]
_sys.modules["magi.db.local_db"] = _sys.modules["magi.db.local_db"]
for _module_name in (
    "models_action_item", "models_contact", "models_control_plane",
    "models_eve_runtime", "models_magic", "models_magis", "models_magis_admin",
    "models_magis_membership", "models_mcp_server", "models_setting",
    "models_token_usage", "models_tool",
):
    _sys.modules[f"magi.db.{_module_name}"] = _sys.modules[
        f"magi.db.{_module_name}"
    ]
_sys.modules["magi.db.settings"] = __import__("magi.db.settings", fromlist=["*"])

# Session-domain tables — owned by ``magi.agent.session``
# but re-exported here for callers that want a single import
# surface (``from magi.db import ChatSession``).
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
    "ControlOperator",
    "ControlSetting",
    "MAGIC",
    "MAGIS",
    "MAGISAdmin",
    "MAGISMembership",
    "MAGISRole",
    "EveRuntime",
    "McpServer",
    "ToolRegistry",
    "ToolDefinitionRecord",
    "ToolCatalogState",
    # dashboard
    "ActionItem",
    "TokenUsage",
    "Setting",
    # sessions (re-exported from sessions/tables.py)
    "ChatSession",
    "ChatMessage",
]
