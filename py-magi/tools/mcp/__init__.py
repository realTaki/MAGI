"""MCP server administration tools.

LLM-callable CRUD tools for configuring MCP servers.
The LLM uses these to help the operator add / list / update /
delete server rows. The :class:`~mcp.worker.McpWorker` does
not call these tools — it only reads the resulting rows from
``bus.mcp_servers_book`` to manage live connections.

Tool sources vs. tool location
------------------------------

The :class:`~mcp.worker.McpWorker` injects the four manage
tools into the tools registry under source ``"mcp_manage"`` and
the discovered MCP server tools under source ``"mcp"``. The
*file location* of the manage tool classes is here under
:mod:`tools.mcp` (alongside the rest of MAGI's builtin
tools); the *registry source* is a separate, runtime concept.

Tools
-----

- :mod:`tools.mcp.add_mcp_server`     — :class:`AddMcpServerTool`
- :mod:`tools.mcp.list_mcp_servers`   — :class:`ListMcpServersTool`
- :mod:`tools.mcp.update_mcp_server`  — :class:`UpdateMcpServerTool`
- :mod:`tools.mcp.delete_mcp_server`  — :class:`DeleteMcpServerTool`

MCP servers are infrastructure configuration. The LLM uses
these tools when the operator asks to add, list, update, or
remove a server.
"""
