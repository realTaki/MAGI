"""Shared MAGI persistence boundaries.

The ORM tables and engine helpers.  All table declarations
have moved to :mod:`magi.bus.models.{local,magis,queue}`
where the bus services own them.  This package now exposes
only the engine / session-factory / runtime configuration:

  - :mod:`.base`                — :class:`Base` declarative class
  - :mod:`.engine`              — private per-MAGI SQLite engine +
                                  ``init_orm`` / ``get_session`` /
                                  ``open_session``
  - :mod:`.magis`               — public PostgreSQL engine and sessions for
                                  the MAGI's one direct MAGIS
  - :mod:`.alembic`            — versioned schema migrations
  - :mod:`.alembic_runner`     — startup ``upgrade head`` integration
  - :mod:`.migrations`         — legacy pre-Alembic adoption pass only
  - :mod:`.local_db`            — raw-SQL ``meta`` KV table
  - :mod:`.settings`            — settings KV (``state_get`` / ``state_set``)
  - :mod:`.runtime_settings`    — typed runtime settings facade

Domain code (agent / tools / channels) goes through
:mod:`magi.bus`; this package is for the bus internals.
"""

from __future__ import annotations

from magi.bus.models.local.action_item import ActionItem
from magi.bus.models.local.contact import Contact, ContactNote
from magi.bus.models.local.control_plane import ControlOperator, ControlSetting
from magi.bus.models.local.mcp_server import McpServer
from magi.bus.models.local.memory import MemoryEntry
from magi.bus.models.local.session import ChatMessage, ChatSession
from magi.bus.models.local.setting import Setting
from magi.bus.models.local.token_usage import TokenUsage
from magi.bus.models.local.tool import ToolCatalogState, ToolDefinitionRecord
from magi.bus.models.magis.auth_credential import AuthCredential
from magi.bus.models.magis.eve_runtime import EveRuntime
from magi.bus.models.magis.magic import MAGIC
from magi.bus.models.magis.magis import MAGIS
from magi.bus.models.magis.magis_admin import MAGISAdmin
from magi.bus.models.magis.magis_membership import MAGISMembership, MAGISRole
from magi.db.base import Base
from magi.db.engine import (
    get_engine,
    get_session,
    init_orm,
    open_session,
    require_state_dir,
)
from magi.db.local_db import init_sqlite

__all__ = [
    # base + engine
    "Base",
    "get_engine",
    "get_session",
    "init_orm",
    "open_session",
    "require_state_dir",
    "init_sqlite",
    # re-exports kept for callers that still import from ``magi.db``
    "ActionItem",
    "AuthCredential",
    "ChatMessage",
    "ChatSession",
    "Contact",
    "ContactNote",
    "ControlOperator",
    "ControlSetting",
    "EveRuntime",
    "MAGIC",
    "MAGIS",
    "MAGISAdmin",
    "MAGISMembership",
    "MAGISRole",
    "McpServer",
    "MemoryEntry",
    "Setting",
    "TokenUsage",
    "ToolCatalogState",
    "ToolDefinitionRecord",
]
