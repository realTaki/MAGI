"""``mcp_server`` tool — list / add / update / delete MCP server rows.

One catalog slot for rare admin ops. ``action`` selects the verb.
This tool is not a direct writer to :class:`McpServerBook`; add /
update / delete publish a
:class:`~bus.firmwares.jobs.changeMCPServerJob.ChangeMCPServerJob`
and wait for :class:`~mcp.worker.McpWorker` to apply the write.
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

_ACTIONS = ("list", "add", "update", "delete")


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


def _merge(current: McpServer, kwargs: dict[str, Any]) -> McpServer:
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


class McpServerTool(BaseTool):
    """List, add, update, or delete an MCP server row."""

    name = "mcp_server"
    description = (
        "Configure MAGI's MCP (Model-Context-Protocol) servers. "
        "Required action: list | add | update | delete. "
        "list: no other fields; env and headers are never returned. "
        "add: name + connection_type (stdio | sse | streamable_http). "
        "stdio needs command (optional args); sse/streamable_http need url. "
        "update: name plus the fields to change (partial overlay). "
        "Rename is not supported — delete then add. "
        "delete: name; a missing name is a no-op. "
        "Optional on add/update: env, headers, enabled, "
        "connect_timeout, execute_timeout, sse_read_timeout. "
        "Confirm with the operator before add/update/delete. "
        "Do not guess env vars."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "description": "Which operation to run.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Server name. Required for add, update, and delete. "
                    "ASCII, no spaces, max 64 chars. Used as the tool-name "
                    "prefix on the LLM menu (e.g. 'github__echo'). Rename "
                    "is not supported."
                ),
            },
            "connection_type": {
                "type": "string",
                "enum": ["stdio", "sse", "streamable_http"],
                "description": "Transport type. Required for add.",
            },
            "command": {
                "type": "string",
                "description": "Required for stdio. Executable name/path (e.g. 'uvx', 'npx').",
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
                "description": "Whether the server is enabled. Default true on add.",
            },
            "env": {
                "type": "object",
                "description": (
                    "Env vars for the server process. Use for API keys. "
                    "A blank value on update clears the key."
                ),
            },
            "headers": {
                "type": "object",
                "description": (
                    "HTTP headers for sse/streamable_http. "
                    "A blank value on update clears the key."
                ),
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
        "required": ["action"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        action = (kwargs.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return ToolResult.err("action must be one of: list, add, update, delete")
        if action == "list":
            return await self._list()
        if action == "add":
            return await self._add(**kwargs)
        if action == "update":
            return await self._update(**kwargs)
        return await self._delete(**kwargs)

    async def _list(self) -> ToolResult:
        rows = self.bus.mcp_servers_book.list_all()
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

    async def _wait(self, job_id: int, *, verb: str) -> ToolResult | None:
        result = await self.bus.change_mcp_server_job_board.wait_for_result(
            job_id=job_id,
        )
        if result is None:
            return ToolResult.err(
                "MCP worker did not process the change within the timeout; "
                "call mcp_server with action=list to verify the new state."
            )
        if result.status != JobStatus.COMPLETED:
            return ToolResult.err(result.error or f"MCP worker failed to {verb}")
        return None

    async def _add(self, **kwargs: Any) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.err("add requires field: name")

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

        if self.bus.mcp_servers_book.get_by_name(name=name) is not None:
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

        job_id = self.bus.change_mcp_server_job_board.publish(
            ChangeMCPServerJob(kind=MCPKind.ADDED, server_name=name, server=server)
        )
        failed = await self._wait(job_id, verb="apply the change")
        if failed is not None:
            return failed

        row = self.bus.mcp_servers_book.get_by_name(name=name)
        if row is None:
            return ToolResult.err(
                "worker reported success but the row is missing; this should not happen"
            )
        return ToolResult.ok(
            {
                "status": "created",
                "server": serialize_mcp_server(row),
                "hint": "Server saved and connected. Verify with mcp_server action=list.",
            }
        )

    async def _update(self, **kwargs: Any) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.err("update requires field: name")

        conn_type = (kwargs.get("connection_type") or "").strip().lower()
        if conn_type and conn_type not in ("stdio", "sse", "streamable_http"):
            return ToolResult.err("connection_type must be one of: stdio, sse, streamable_http")

        if conn_type == "stdio" and "command" in kwargs and not kwargs.get("command"):
            return ToolResult.err("stdio servers require 'command'")
        if conn_type in ("sse", "streamable_http") and "url" in kwargs and not kwargs.get("url"):
            return ToolResult.err(f"{conn_type} servers require 'url'")

        current = self.bus.mcp_servers_book.get_by_name(name=name)
        if current is None:
            return ToolResult.err(
                f"server '{name}' does not exist. Create it with mcp_server action=add first."
            )

        new_server = _merge(current, kwargs)
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

        failed = await self._wait(job_id, verb="apply the update")
        if failed is not None:
            return failed

        row = self.bus.mcp_servers_book.get_by_name(name=name)
        if row is None:
            return ToolResult.err(
                "worker reported success but the row is missing; this should not happen"
            )
        return ToolResult.ok(
            {
                "status": "updated",
                "server": serialize_mcp_server(row),
                "hint": "Server updated and reloaded. The new configuration is live.",
            }
        )

    async def _delete(self, **kwargs: Any) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.err("delete requires field: name")

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
        failed = await self._wait(job_id, verb="delete the server")
        if failed is not None:
            return failed

        return ToolResult.ok(
            {
                "status": "deleted",
                "name": name,
                "hint": "Server removed and disconnected. The tools it surfaced are gone.",
            }
        )
