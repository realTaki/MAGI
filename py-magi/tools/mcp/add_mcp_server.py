"""``add_mcp_server`` tool — create a new MCP server row.

The LLM is expected to interact with the operator to collect the
right fields before calling this tool — ``name`` and
``connection_type`` are always required, ``command``+``args`` for
``stdio``, ``url`` for ``sse`` / ``streamable_http``.

This tool is **not** a direct writer to :class:`McpServerBook`.
It builds the full :class:`McpServer` DTO, publishes a
:class:`~bus.firmwares.jobs.changeMCPServerJob.ChangeMCPServerJob`
with ``kind="added"``, and waits for the
:class:`~mcp.worker.McpWorker` to apply the write + reconnect.
The Worker is the single writer — see
:mod:`mcp.worker` for the rationale.

JSON columns are exposed to the LLM as plain dicts (``env`` /
``headers``) — the Job payload serialises to the underlying
``env`` / ``headers`` JSON columns automatically.
"""

from __future__ import annotations

from typing import Any

from old_bus.bases.job import JobStatus
from old_bus.firmwares.books.local.mcpServerBook import (
    McpServer,
    serialize_mcp_server,
)
from old_bus.firmwares.jobs import ChangeMCPServerJob, MCPKind
from tools.base import Tool, ToolContext, ToolResult


def _build_server(
    *,
    name: str,
    connection_type: str,
    command: str | None,
    args: list[str] | None,
    url: str | None,
    env: dict[str, str] | None,
    headers: dict[str, str] | None,
    enabled: bool,
    connect_timeout: float | None,
    execute_timeout: float | None,
    sse_read_timeout: float | None,
) -> McpServer:
    """Build a fully-populated :class:`McpServer` DTO from tool kwargs.

    Database-owned ``id`` and audit timestamps are intentionally absent;
    the Book owns them when the Worker persists this request.
    """
    return McpServer(
        name=name,
        connection_type=connection_type,
        command=command,
        args=list(args or []),
        url=url,
        env=dict(env or {}),
        headers=dict(headers or {}),
        enabled=enabled,
        connect_timeout=connect_timeout,
        execute_timeout=execute_timeout,
        sse_read_timeout=sse_read_timeout,
    )


class AddMcpServerTool(Tool):
    """Create a new MCP server. Requires name + connection_type."""

    name = "add_mcp_server"
    ALLOWED_ROLES = frozenset({"admin"})
    description = (
        "Add a new MCP (Model-Context-Protocol) server. "
        "The operator must provide at least ``name`` and "
        "``connection_type`` (one of: stdio, sse, streamable_http). "
        "For stdio: ``command`` (required) + optional ``args`` array. "
        "For sse/streamable_http: ``url`` (required). "
        "Optional: ``env`` dict (env vars for the server process — "
        "use this to pass API keys etc.), ``headers`` dict (HTTP headers), "
        "``enabled`` (default true), ``connect_timeout`` / "
        "``execute_timeout`` / ``sse_read_timeout`` (seconds, optional). "
        "Before calling, confirm with the operator: the server name, "
        "connection type, and the required fields for that type. "
        "Do NOT guess env vars — ask the operator what to put in them."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Unique server name (ASCII, no spaces, max 64 chars). "
                    "Used as the tool-name prefix on the LLM's menu "
                    "(e.g. 'github__echo')."
                ),
            },
            "connection_type": {
                "type": "string",
                "enum": ["stdio", "sse", "streamable_http"],
                "description": "Transport type.",
            },
            "command": {
                "type": "string",
                "description": "Required for stdio. Executable name/path (e.g. 'uvx', 'npx').",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CLI args for stdio servers (e.g. ['github-coding-plan-mcp', '-y']).",
            },
            "url": {
                "type": "string",
                "description": "Required for sse/streamable_http. Full URL of the MCP endpoint.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether to enable immediately. Default true.",
            },
            "env": {
                "type": "object",
                "description": (
                    "Env vars for the server process. Use for API keys: "
                    "e.g. {'GITHUB_API_KEY': 'ghp_...'}."
                ),
            },
            "headers": {
                "type": "object",
                "description": "HTTP headers for sse/streamable_http servers.",
            },
            "connect_timeout": {
                "type": "number",
                "description": "Connection timeout in seconds. Default 10.",
            },
            "execute_timeout": {
                "type": "number",
                "description": "Per-tool execution timeout in seconds. Default 60.",
            },
            "sse_read_timeout": {
                "type": "number",
                "description": "SSE read timeout in seconds. Default 120.",
            },
        },
        "required": ["name", "connection_type"],
    }

    @Tool.require_bus
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        assert ctx.bus is not None, "require_bus should have caught this"
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.err("missing required field: name")

        conn_type = (kwargs.get("connection_type") or "").strip().lower()
        if conn_type not in ("stdio", "sse", "streamable_http"):
            return ToolResult.err("connection_type must be one of: stdio, sse, streamable_http")

        if conn_type == "stdio" and not kwargs.get("command"):
            return ToolResult.err("stdio servers require 'command'")

        if conn_type != "stdio" and not kwargs.get("url"):
            return ToolResult.err(f"{conn_type} servers require 'url'")

        enabled = kwargs.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = True

        # Existence check via a read — the Worker is the writer,
        # but a stale read here would mask the "already exists"
        # case (the Job Board doesn't reject duplicates either).
        if ctx.bus.mcp_servers_book.get_by_name(name=name) is not None:
            return ToolResult.err(f"server '{name}' already exists")

        server = _build_server(
            name=name,
            connection_type=conn_type,
            command=kwargs.get("command"),
            args=kwargs.get("args"),
            url=kwargs.get("url"),
            env=kwargs.get("env"),
            headers=kwargs.get("headers"),
            enabled=enabled,
            connect_timeout=kwargs.get("connect_timeout"),
            execute_timeout=kwargs.get("execute_timeout"),
            sse_read_timeout=kwargs.get("sse_read_timeout"),
        )

        job_id = ctx.bus.change_mcp_server_job_board.publish(
            ChangeMCPServerJob(kind=MCPKind.ADDED, server_name=name, server=server)
        )

        # Wait for the Worker to upsert + connect so the LLM
        # gets immediate feedback. ``None`` means the worker
        # hasn't answered yet (DB blip / poll lag).
        result = await ctx.bus.change_mcp_server_job_board.wait_for_result(
            job_id=job_id,
        )
        if result is None:
            return ToolResult.err(
                "MCP worker did not process the change within the timeout; "
                "list_mcp_servers to verify the new state."
            )
        if result.status != JobStatus.COMPLETED:
            return ToolResult.err(result.error or "MCP worker failed to apply the change")

        # Read the row back so the LLM sees the real autoincrement
        # id / timestamps / any default fills.
        row = ctx.bus.mcp_servers_book.get_by_name(name=name)
        if row is None:
            return ToolResult.err(
                "worker reported success but the row is missing; this should not happen"
            )
        return ToolResult.ok(
            {
                "status": "created",
                "server": serialize_mcp_server(row),
                "hint": ("Server saved and connected. Verify with list_mcp_servers."),
            }
        )


__all__ = ["AddMcpServerTool"]
