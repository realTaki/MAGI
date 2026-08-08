"""MCP — Model-Context-Protocol subsystem.

MCP is a first-class extension surface in MAGI: the runtime
holds long-lived connections to operator-configured MCP servers
and surfaces their tools to the agent loop.

Architecture
------------

After the worker refactor (see ``docs/MCP_WORKER_DESIGN.md``),
this package no longer owns the long-lived subprocesses itself.
The :class:`~magi.mcp.worker.McpWorker` is the single
lifecycle owner for every MCP connection in one MAGI process;
the loader is reduced to a small library of primitives the
worker composes.

::

    McpWorker
      ├─ reads from bus.mcp_servers_book   (new_bus McpServerBook)
      ├─ writes McpServerChangedJob        (new_bus Job Board)
      └─ injects tools into
         magi.tools.registry.register_tools("mcp", ...)
         → on_tools_changed listener → ToolsWorker re-publishes catalog

Module layout
-------------

- :mod:`magi.mcp.loader` — :class:`MCPServerConnection` /
  :class:`MCPTool` / :class:`MCPTimeoutConfig` (the small set of
  primitives the worker composes). The previous module-level
  ``_connections`` cache, ``load_mcp_tools_async`` /
  ``load_mcp_tools_blocking``, ``list_tools_for_server``,
  ``cleanup_mcp_connections`` and ``active_connections`` were
  removed — the worker is the only connection owner now, and
  the WebUI detail page reads the Book directly when it needs
  metadata.
- :mod:`magi.mcp.worker` — :class:`McpWorker` plus
  :func:`start_mcp_worker` / :func:`stop_mcp_worker` lifecycle
  helpers. Started by :mod:`magi.startup.runtime` immediately
  after :class:`~magi.tools.worker.ToolsWorker`.
- :mod:`magi.mcp.manage` — LLM-callable CRUD tools
  (``add_mcp_server`` / ``list_mcp_servers`` /
  ``update_mcp_server`` / ``delete_mcp_server``). Admin-only;
  the LLM uses these to help the operator configure servers.
- :mod:`magi.mcp.sharing` — *future*. MAGIS-level sharing of
  MCP server configs. Defining point only today; the table /
  API / LLM tools land in a follow-up PR.

The data path that the WebUI / LLM manage tools still write to
is the old bus ``mcp_servers`` table (see
``magi/bus/jobs/services/mcp.py``). The worker reads via
``bus.mcp_servers_book``, which points at the same physical
SQLite table through a parallel new_bus ORM (see
``magi/new_bus/library/local/mcpServerBook.py``). They share
the row storage; the new_bus Book owns the new write path that
the API / manage tools will migrate onto in a follow-up.
"""

from __future__ import annotations

from magi.mcp.loader import (
    MCPTimeoutConfig,
    MCPServerConnection,
    MCPTool,
)
from magi.mcp.manage import (
    AddMcpServerTool,
    DeleteMcpServerTool,
    ListMcpServersTool,
    UpdateMcpServerTool,
)
from magi.mcp.worker import McpWorker, start_mcp_worker, stop_mcp_worker

__all__ = [
    # Loader primitives
    "MCPTimeoutConfig",
    "MCPServerConnection",
    "MCPTool",
    # Worker + lifecycle helpers
    "McpWorker",
    "start_mcp_worker",
    "stop_mcp_worker",
    # LLM manage tools
    "AddMcpServerTool",
    "ListMcpServersTool",
    "UpdateMcpServerTool",
    "DeleteMcpServerTool",
]
