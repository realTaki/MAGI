"""MCP — Model-Context-Protocol subsystem.

MCP is a first-class extension surface in MAGI: the runtime
holds long-lived connections to operator-configured MCP servers
and surfaces their tools to the agent loop.

Architecture
------------

After the worker refactor (see ``docs/MCP_WORKER_DESIGN.md``),
this package no longer owns the long-lived subprocesses itself.
The :class:`~mcp.worker.McpWorker` is the single
lifecycle owner for every MCP connection in one MAGI process;
the loader is reduced to a small library of primitives the
worker composes.

::

    McpWorker (sole writer)
      ├─ reads from bus.mcp_servers_book   (bus McpServerBook)
      ├─ writes ChangeMCPServerJob         (bus Job Board, payload
      │                                     carries the full DTO)
      └─ injects discovered tools into
         tools.registry.register_tools("mcp", ...)
         → on_tools_changed listener → ToolsWorker re-publishes catalog

The manage tool (``mcp_server``, actions list / add / update /
delete) lives in :mod:`tools.mcp` and is registered as a
builtin by :mod:`tools.registry`. It publishes
to ``bus.change_mcp_server_job_board`` and waits for the worker
to apply the change; the worker is the **only** writer to the
Book so the LLM's view of the world and the live connections
stay in sync.

Module layout
-------------

- :mod:`mcp.MCPClient` — :class:`MCPServerConnection` /
  :class:`MCPTool` / :class:`MCPTimeoutConfig` (the small set of
  primitives the worker composes). The previous module-level
  ``_connections`` cache, ``load_mcp_tools_async`` /
  ``load_mcp_tools_blocking``, ``list_tools_for_server`,
  ``cleanup_mcp_connections` and ``active_connections` were
  removed — the worker is the only connection owner now, and
  the WebUI detail page reads the Book directly when it needs
  metadata.
- :mod:`mcp.worker` — :class:`McpWorker`, started by the
  startup-owned :class:`startup.workers.WorkerRegistry`.
- :mod:`mcp.sharing` — *future*. MAGIS-level sharing of
  MCP server configs. Defining point only today; the table /
  API / LLM tools land in a follow-up PR.

The data path that the WebUI / LLM manage tools write to is
the bus ``McpServerBook`` (via ``ChangeMCPServerJob``);
the Worker is the sole writer. The WebUI / ``McpService``-backed
read paths still resolve through the same physical SQLite
table (see ``magi/bus/firmwares/books/local/mcpServerBook.py``).
"""

from __future__ import annotations

from mcp.MCPClient import (
    MCPServerConnection,
    MCPTimeoutConfig,
    MCPTool,
)
from mcp.worker import McpWorker

__all__ = [
    # Client primitives
    "MCPTimeoutConfig",
    "MCPServerConnection",
    "MCPTool",
    # Worker
    "McpWorker",
]
