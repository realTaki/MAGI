"""MCP — Model-Context-Protocol subsystem.

DESIGN (this docstring is the canonical reference; everything
else in this package is scaffolding around it).

Why a top-level package
-----------------------

MCP is a first-class extension surface in MAGI: the runtime
hot-wires ``mcp_servers`` table rows into the LLM's tool
menu, with admin-only create / update / delete lifecycle
managed by both WebUI APIs and LLM tools. The crossing
points are:

  - long-lived subprocess / SSE / streamable-HTTP sockets
  - tool discovery (``list_tools`` / ``call_tool``)
  - server-level CRUD against the ``mcp_servers`` table
  - future: cross-MAGIS sharing so one operator's
    configured server can be exposed to other MAGICS
    under the same MAGIS

These concerns are orthogonal to the built-in tools in
:mod:`magi.tools` (the LLM's "what can I call" surface).
The tools package owns **callable skills**; this package
owns **how MCP servers become skills**. Splitting them
keeps each module focused and lets the MCP features
grow (sharing, marketplace, multi-tenant scopes) without
polling the tools package.

Module layout
-------------

  - :mod:`magi.mcp.loader` — DB-backed load + connect.
    Reads the ``mcp_servers`` table, opens one
    ``MCPServerConnection`` per row, wraps each tool as
    a :class:`magi.tools.base.Tool`-shaped wrapper.

  - :mod:`magi.mcp.manage` — LLM-callable CRUD tools
    (``add_mcp_server`` / ``list_mcp_servers`` /
    ``update_mcp_server`` / ``delete_mcp_server``).
    Admin-only; the LLM uses these to help the operator
    configure servers.

  - :mod:`magi.mcp.sharing` — *future*. MAGIS-level
    sharing of MCP server configs. Defining point only
    today; the table / API / LLM tools land in a follow-up
    PR.

Configuration surface
---------------------

The single source of truth is the ``mcp_servers`` table
(:class:`magi.bus.models.local.mcp_server.McpServer`). The
WebUI ``/api/mcp-servers`` endpoints
(:mod:`magi.channels.webui.api.mcp_servers`) and the LLM
manage tools (:mod:`magi.mcp.manage`) both write to it.
The loader reads it on every ``maybe_reload_mcp_tools``
trigger. No JSON file, no ``MAGI_MCP_*`` env vars — the
operator-facing surface is the WebUI card, not a deploy
manifest.
"""

from __future__ import annotations

from magi.mcp.loader import (
    MCPTimeoutConfig,
    MCPServerConnection,
    MCPTool,
    active_connections,
    cleanup_mcp_connections,
    list_tools_for_server,
    load_mcp_tools_async,
    load_mcp_tools_blocking,
)
from magi.mcp.manage import (
    AddMcpServerTool,
    DeleteMcpServerTool,
    ListMcpServersTool,
    UpdateMcpServerTool,
)

__all__ = [
    # Loader surface
    "MCPTimeoutConfig",
    "MCPServerConnection",
    "MCPTool",
    "load_mcp_tools_async",
    "load_mcp_tools_blocking",
    "cleanup_mcp_connections",
    "active_connections",
    "list_tools_for_server",
    # LLM manage tools
    "AddMcpServerTool",
    "ListMcpServersTool",
    "UpdateMcpServerTool",
    "DeleteMcpServerTool",
]
