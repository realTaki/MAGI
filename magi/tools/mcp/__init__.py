"""MCP server administration tools.

LLM-callable CRUD tools for configuring MCP servers. Admin-only;
the LLM uses these to help the operator add / list / update /
delete server rows. The :class:`~magi.mcp.worker.McpWorker` does
not call these tools — it only reads the resulting rows from
``bus.mcp_servers_book`` to manage live connections.

Tool sources vs. tool location
------------------------------

The :class:`~magi.mcp.worker.McpWorker` injects the four manage
tools into the tools registry under source ``"mcp_manage"`` and
the discovered MCP server tools under source ``"mcp"``. The
*file location* of the manage tool classes is here under
:mod:`magi.tools.mcp` (alongside the rest of MAGI's builtin
tools); the *registry source* is a separate, runtime concept.

Tools
-----

- :mod:`magi.tools.mcp.add_mcp_server`     — :class:`AddMcpServerTool`
- :mod:`magi.tools.mcp.list_mcp_servers`   — :class:`ListMcpServersTool`
- :mod:`magi.tools.mcp.update_mcp_server`  — :class:`UpdateMcpServerTool`
- :mod:`magi.tools.mcp.delete_mcp_server`  — :class:`DeleteMcpServerTool`

Scope (admin-only): MCP servers are infrastructure — only
``admin`` operators can create / update / delete them. The
``ALLOWED_ROLES`` constant on each tool is catalog metadata
for the agent menu.
"""
