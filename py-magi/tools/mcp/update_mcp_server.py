"""``update_mcp_server`` tool — edit an existing MCP server row.

Every mutable field is overridden by the body — there is no
partial-merge semantics. The operator sends the complete picture
they want; the LLM is expected to merge "what was there before"
with the operator's edit before calling this tool. Run
``list_mcp_servers`` first to read the current state.

The ``name`` (path id) is the source of truth — passing a
different ``name`` in the body is rejected. To rename, delete +
create.

Blank env / header values are treated as "clear this key"
(writes ``""`` to the JSON column). The loader reads ``""`` as
"operator explicitly set this to empty, do not inherit from
parent env". Same contract the WebUI ``PATCH`` endpoint enforces.

This tool is **not** a direct writer to :class:`McpServerBook`.
It builds the full :class:`McpServer` DTO, publishes a
:class:`~bus.firmwares.jobs.changeMCPServerJob.ChangeMCPServerJob`
with ``kind="updated"`` (or ``kind="toggled"`` if the body only
changed the ``enabled`` flag), and waits for the
:class:`~mcp.worker.McpWorker` to apply the write + reconnect.
The Worker is the single writer — see :mod:`mcp.worker`.
"""

from __future__ import annotations

from typing import Any

from old_bus.bases.job import JobStatus
from old_bus.firmwares.books.local.mcpServerBook import (
    McpServer,
    serialize_mcp_server,
)
from old_bus.firmwares.jobs import ChangeMCPServerJob, MCPKind
from tools.BaseTool import BaseTool, ToolResult


def _merge(current: McpServer, kwargs: dict[str, Any]) -> McpServer:
    """Return a new :class:`McpServer` reflecting *kwargs* overlaid on *current*.

    Only the fields explicitly listed in *kwargs* get overwritten;
    everything else carries over from *current*. Database-owned identity
    and audit fields are not part of a new write request.
    """
    return McpServer(
        name=current.name,
        connection_type=kwargs.get("connection_type", current.connection_type),
        command=kwargs.get("command", current.command),
        args=list(kwargs.get("args", current.args)),
        url=kwargs.get("url", current.url),
        env=dict(kwargs.get("env", dict(current.env))),
        headers=dict(kwargs.get("headers", dict(current.headers))),
        enabled=bool(kwargs.get("enabled", current.enabled)),
        connect_timeout=kwargs.get("connect_timeout", current.connect_timeout),
        execute_timeout=kwargs.get("execute_timeout", current.execute_timeout),
        sse_read_timeout=kwargs.get("sse_read_timeout", current.sse_read_timeout),
    )


class UpdateMcpServerTool(BaseTool):
    """Update an existing MCP server's fields."""

    name = "update_mcp_server"
    description = (
        "Update an existing MCP server by name. The new "
        "state replaces the existing row entirely — "
        "partial merge is not supported. The LLM should "
        "call ``list_mcp_servers`` first to read the "
        "current row, then send back the full intended "
        "state. To rename, delete + create. The ``name`` "
        "in the body must match the path name; a mismatch "
        "returns an error so a typo doesn't silently "
        "create a new server. Before calling, confirm "
        "with the operator which fields are changing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Existing server name (must match the "
                    "server you want to update). Echoed "
                    "back in the body for symmetry with "
                    "``add_mcp_server``; rename is not "
                    "supported."
                ),
            },
            "connection_type": {
                "type": "string",
                "enum": ["stdio", "sse", "streamable_http"],
                "description": "Transport type.",
            },
            "command": {
                "type": "string",
                "description": ("Required for stdio. Executable name/path (e.g. 'uvx', 'npx')."),
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CLI args for stdio servers.",
            },
            "url": {
                "type": "string",
                "description": "Required for sse/streamable_http. Full URL of the MCP endpoint.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether to enable this server.",
            },
            "env": {
                "type": "object",
                "description": (
                    "Env vars for the server process. Use "
                    "for API keys: e.g. {'GITHUB_API_KEY': 'ghp_...'}. "
                    "A blank value clears the key."
                ),
            },
            "headers": {
                "type": "object",
                "description": (
                    "HTTP headers for sse/streamable_http servers. A blank value clears the key."
                ),
            },
            "connect_timeout": {
                "type": "number",
                "description": "Connection timeout in seconds.",
            },
            "execute_timeout": {
                "type": "number",
                "description": "Per-tool execution timeout in seconds.",
            },
            "sse_read_timeout": {
                "type": "number",
                "description": "SSE read timeout in seconds.",
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

        conn_type = (kwargs.get("connection_type") or "").strip().lower()
        if conn_type and conn_type not in ("stdio", "sse", "streamable_http"):
            return ToolResult.err("connection_type must be one of: stdio, sse, streamable_http")

        # Conditional validation: only run when the field
        # was supplied. The operator might be patching a
        # single field and leaving connection_type unset,
        # in which case we keep the existing transport.
        if conn_type == "stdio" and "command" in kwargs and not kwargs.get("command"):
            return ToolResult.err("stdio servers require 'command'")
        if conn_type in ("sse", "streamable_http") and "url" in kwargs and not kwargs.get("url"):
            return ToolResult.err(f"{conn_type} servers require 'url'")

        current = self.bus.mcp_servers_book.get_by_name(name=name)
        if current is None:
            return ToolResult.err(
                f"server '{name}' does not exist. Create it with add_mcp_server first."
            )

        new_server = _merge(current, kwargs)

        # Pick the kind: a pure enable flip is "toggled"; any
        # other field change is "updated". The Worker treats
        # both the same (re-read + reconnect), but the audit
        # trail is friendlier with the finer-grained label.
        changed = {
            k
            for k in (
                "connection_type",
                "command",
                "args",
                "url",
                "enabled",
                "env",
                "headers",
                "connect_timeout",
                "execute_timeout",
                "sse_read_timeout",
            )
            if k in kwargs
        }
        if changed == {"enabled"}:
            job_id = self.bus.change_mcp_server_job_board.publish(
                ChangeMCPServerJob(
                    kind=MCPKind.TOGGLED,
                    server_name=name,
                    new_enabled=new_server.enabled,
                )
            )
        else:
            job_id = self.bus.change_mcp_server_job_board.publish(
                ChangeMCPServerJob(
                    kind=MCPKind.UPDATED,
                    server_name=name,
                    server=new_server,
                )
            )

        result = await self.bus.change_mcp_server_job_board.wait_for_result(
            job_id=job_id,
        )
        if result is None:
            return ToolResult.err(
                "MCP worker did not process the update within the "
                "timeout; list_mcp_servers to verify the new state."
            )
        if result.status != JobStatus.COMPLETED:
            return ToolResult.err(result.error or "MCP worker failed to apply the update")

        row = self.bus.mcp_servers_book.get_by_name(name=name)
        if row is None:
            return ToolResult.err(
                "worker reported success but the row is missing; this should not happen"
            )
        return ToolResult.ok(
            {
                "status": "updated",
                "server": serialize_mcp_server(row),
                "hint": ("Server updated and reloaded. The new configuration is live."),
            }
        )


__all__ = ["UpdateMcpServerTool"]
