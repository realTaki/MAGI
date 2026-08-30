"""``delete_mcp_server`` tool — remove an MCP server row.

Idempotent — silently succeeds if the server doesn't exist.

This tool is **not** a direct writer to :class:`McpServerBook`.
It publishes a
:class:`~bus.firmwares.jobs.changeMCPServerJob.ChangeMCPServerJob`
with ``kind="deleted"`` and waits for the
:class:`~mcp.worker.McpWorker` to apply the delete + tear
down the live connection. The Worker is the single writer — see
:mod:`mcp.worker` for the rationale.
"""

from __future__ import annotations

from typing import Any

from old_bus.firmwares.jobs import MCPKind, ChangeMCPServerJob
from old_bus.bases.job import JobStatus
from tools.BaseTool import BaseTool, ToolResult


class DeleteMcpServerTool(BaseTool):
    """Remove an MCP server by name. Idempotent — silently succeeds
    if the server doesn't exist."""

    name = "delete_mcp_server"
    ALLOWED_ROLES = frozenset({"admin"})
    description = (
        "Delete an MCP server by name. Removing a server also "
        "removes all tools it surfaced. If the server doesn't "
        "exist, nothing happens (no error). Before calling, "
        "confirm the server name with the operator. "
        "Input: name (required)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Server name to delete.",
            },
        },
        "required": ["name"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.err("missing required field: name")

        # Existence check — the Job Board itself doesn't reject
        # duplicate deletes, but we want the "not_found" envelope
        # to skip the worker round-trip when nothing would change.
        if self.bus.mcp_servers_book.get_by_name(name=name) is None:
            return ToolResult.ok(
                {
                    "status": "not_found",
                    "hint": f"No server named '{name}' — nothing to delete.",
                }
            )

        job_id = self.bus.change_mcp_server_job_board.publish(
            ChangeMCPServerJob(kind=MCPKind.DELETED, server_name=name)
        )

        result = await self.bus.change_mcp_server_job_board.wait_for_result(
            job_id=job_id,
        )
        if result is None:
            return ToolResult.err(
                "MCP worker did not process the deletion within the "
                "timeout; list_mcp_servers to verify the new state."
            )
        if result.status != JobStatus.COMPLETED:
            return ToolResult.err(result.error or "MCP worker failed to delete the server")

        return ToolResult.ok(
            {
                "status": "deleted",
                "name": name,
                "hint": ("Server removed and disconnected. The tools it surfaced are gone."),
            }
        )


__all__ = ["DeleteMcpServerTool"]
