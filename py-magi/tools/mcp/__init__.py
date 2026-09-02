"""MCP server administration tools.

One LLM-callable tool, :class:`McpServerTool` (``mcp_server``),
covers list / add / update / delete. The
:class:`~mcp.worker.McpWorker` does not call this tool — it
only reads the resulting rows from ``bus.mcp_servers_book``
to manage live connections.

Discovered MCP server tools are injected under source
``"mcp"``. The manage tool itself is a builtin.
"""
