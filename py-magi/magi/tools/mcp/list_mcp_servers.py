"""``list_mcp_servers`` tool — return all configured MCP servers.

Read-only. Metadata only (name, type, enabled, timeouts) — the
``env`` / ``headers`` columns carry API keys and tokens and are
intentionally never serialised.

Reads through the bus
:class:`~magi.bus.firmwares.books.local.mcpServerBook.McpServerBook.list_all`
so the result reflects every row the
:class:`~magi.mcp.worker.McpWorker` would see on its next
bootstrap. The bus ``McpService.list()`` continues to back
the WebUI in the meantime; both sides share the same physical
SQLite table.
"""

from __future__ import annotations

from typing import Any

from magi.old_bus.firmwares.books.local.mcpServerBook import serialize_mcp_server
from magi.tools.base import Tool, ToolContext, ToolResult


class ListMcpServersTool(Tool):
    """List all configured MCP servers (metadata only)."""

    name = "list_mcp_servers"
    ALLOWED_ROLES = frozenset({"admin"})
    description = (
        "List all configured MCP servers with their metadata "
        "(name, type, enabled status, timeouts). Env vars "
        "and headers are never shown for security."
    )
    input_schema = {"type": "object", "properties": {}}

    @Tool.require_bus
    async def run(self, ctx: ToolContext, **_kwargs: Any) -> ToolResult:
        assert ctx.bus is not None, "require_bus should have caught this"
        rows = ctx.bus.mcp_servers_book.list_all()

        if not rows:
            return ToolResult.ok(
                {
                    "servers": [],
                    "hint": "No MCP servers configured yet.",
                }
            )

        return ToolResult.ok(
            {
                "servers": [serialize_mcp_server(r) for r in rows],
                "count": len(rows),
            }
        )


__all__ = ["ListMcpServersTool"]
