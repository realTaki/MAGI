
"""LLM tools for managing MCP servers.

Four tools:

  - :class:`AddMcpServerTool`    — create a new MCP server row.
  - :class:`ListMcpServersTool`  — list configured servers
    (name, type, enabled, timeout).
  - :class:`UpdateMcpServerTool` — edit an existing row's
    command / args / env / headers / timeouts / enabled
    flag. The ``name`` is the primary key and is
    immutable — rename by delete + create.
  - :class:`DeleteMcpServerTool` — remove a server by name.

Scope (admin-only): MCP servers are infrastructure — only
``admin`` operators can create / update / delete them. The
tools won't appear in the LLM menu for non-admins.

The LLM is expected to interact with the operator to
collect the right fields before calling ``add_mcp_server``
or ``update_mcp_server`` — some fields are required
(name, connection_type), others are conditional
(command+args for stdio, url for sse/http).

Why JSON columns are exposed as plain dicts in the schema:
the LLM doesn't know JSON column internals; it sees
``"env": {"KEY": "value"}`` — a natural dict. The tool
serialises to the ``env_json`` / ``headers_json`` columns
that :mod:`magi.mcp.loader` reads.

Subsystem location
------------------

This module lives in :mod:`magi.mcp` (top-level MCP
subsystem), not under ``magi.tools``. The Tool classes
still subclass :class:`magi.tools.base.Tool` — that's the
shape every tool in the registry takes — but the *file*
sits next to the rest of the MCP package.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from magi.db import McpServer, open_session
from magi.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    caller_role_denied_reason,
)

logger = logging.getLogger("magi.mcp.manage")

# Only admins may create / delete MCP servers.
_ALLOWED_ROLES = frozenset({"admin"})


def _gate(ctx: ToolContext) -> str | None:
    return caller_role_denied_reason(ctx, _ALLOWED_ROLES)


def _err(msg: str) -> ToolResult:
    return ToolResult(content=msg, is_error=True)


def _ok(payload: Any) -> ToolResult:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return ToolResult(content=body, is_error=False)


def _serialize(r: McpServer) -> dict[str, Any]:
    return {
        "name": r.name,
        "connection_type": r.connection_type,
        "command": r.command,
        "args": json.loads(r.args_json) if r.args_json else [],
        "url": r.url,
        "enabled": r.enabled,
        "connect_timeout": r.connect_timeout,
        "execute_timeout": r.execute_timeout,
        "sse_read_timeout": r.sse_read_timeout,
        # Never expose env / headers values — they carry
        # API keys. The operator can inspect them in the UI.
    }


# -- AddMcpServerTool ---------------------------------------------------------


class AddMcpServerTool(Tool):
    """Create a new MCP server. Requires name + connection_type."""

    name = "add_mcp_server"
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
                    "(e.g. 'minimax__echo')."
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
                "description": "CLI args for stdio servers (e.g. ['minimax-coding-plan-mcp', '-y']).",
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
                    "e.g. {'MINIMAX_API_KEY': 'sk-...', 'MINIMAX_GROUP_ID': '...'}."
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

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        denied = _gate(context)
        if denied:
            return _err(denied)

        name = (kwargs.get("name") or "").strip()
        if not name:
            return _err("missing required field: name")

        conn_type = (kwargs.get("connection_type") or "").strip().lower()
        if conn_type not in ("stdio", "sse", "streamable_http"):
            return _err(
                "connection_type must be one of: stdio, sse, streamable_http"
            )

        if conn_type == "stdio" and not kwargs.get("command"):
            return _err("stdio servers require 'command'")

        if conn_type != "stdio" and not kwargs.get("url"):
            return _err(
                f"{conn_type} servers require 'url'"
            )

        enabled = kwargs.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = True

        with open_session() as s:
            existing = s.execute(
                select(McpServer).where(McpServer.name == name)
            ).scalar_one_or_none()
            if existing:
                return _err(
                    f"server '{name}' already exists. Delete it first "
                    f"with delete_mcp_server if you want to recreate it."
                )

            row = McpServer(
                name=name,
                connection_type=conn_type,
                command=kwargs.get("command"),
                args_json=json.dumps(kwargs.get("args") or []),
                url=kwargs.get("url"),
                enabled=enabled,
                connect_timeout=kwargs.get("connect_timeout"),
                execute_timeout=kwargs.get("execute_timeout"),
                sse_read_timeout=kwargs.get("sse_read_timeout"),
                env_json=json.dumps(kwargs.get("env") or {}),
                headers_json=json.dumps(kwargs.get("headers") or {}),
            )
            s.add(row)
            s.commit()
            logger.info(
                "added MCP server %s (type=%s, enabled=%s)",
                name, conn_type, enabled,
            )

        return _ok({
            "status": "created",
            "server": _serialize(row),
            "hint": (
                "Server saved. It will be loaded on the next chat turn. "
                "If you want to verify it, call list_mcp_servers."
            ),
        })


# -- ListMcpServersTool -------------------------------------------------------


class ListMcpServersTool(Tool):
    """List all configured MCP servers (metadata only)."""

    name = "list_mcp_servers"
    description = (
        "List all configured MCP servers with their metadata "
        "(name, type, enabled status, timeouts). Env vars "
        "and headers are never shown for security."
    )
    input_schema = {"type": "object", "properties": {}}

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        denied = _gate(context)
        if denied:
            return _err(denied)

        with open_session() as s:
            rows = s.execute(
                select(McpServer).order_by(McpServer.name)
            ).scalars().all()

        if not rows:
            return _ok({"servers": [], "hint": "No MCP servers configured yet."})

        return _ok({
            "servers": [_serialize(r) for r in rows],
            "count": len(rows),
        })


# -- DeleteMcpServerTool ------------------------------------------------------


class DeleteMcpServerTool(Tool):
    """Remove an MCP server by name. Idempotent — silently succeeds
    if the server doesn't exist."""

    name = "delete_mcp_server"
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

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        denied = _gate(context)
        if denied:
            return _err(denied)

        name = (kwargs.get("name") or "").strip()
        if not name:
            return _err("missing required field: name")

        with open_session() as s:
            row = s.execute(
                select(McpServer).where(McpServer.name == name)
            ).scalar_one_or_none()

            if row is None:
                return _ok({
                    "status": "not_found",
                    "hint": f"No server named '{name}' — nothing to delete.",
                })

            s.delete(row)
            s.commit()
            logger.info("deleted MCP server %s", name)

        return _ok({
            "status": "deleted",
            "name": name,
            "hint": "Server removed. The tools it surfaced will disappear on the next chat turn.",
        })


# -- UpdateMcpServerTool ------------------------------------------------------


class UpdateMcpServerTool(Tool):
    """Update an existing MCP server's fields.

    The ``name`` (path id) is the source of truth — passing
    a different ``name`` in the body is rejected. To rename,
    delete + create.

    Every mutable field is overridden by the body — there
    is no partial-merge semantics. The operator sends the
    complete picture they want; the LLM is expected to
    merge "what was there before" with the operator's edit
    before calling this tool. ``list_mcp_servers`` first
    to read the current state.

    Blank env / header values are treated as "clear this
    key" (writes ``""`` to the JSON column). The loader
    reads ``""`` as "operator explicitly set this to
    empty, do not inherit from parent env". Same contract
    the WebUI ``PATCH`` endpoint enforces.
    """

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
                "description": (
                    "Required for stdio. Executable name/path "
                    "(e.g. 'uvx', 'npx')."
                ),
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CLI args for stdio servers.",
            },
            "url": {
                "type": "string",
                "description": (
                    "Required for sse/streamable_http. Full "
                    "URL of the MCP endpoint."
                ),
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether to enable this server.",
            },
            "env": {
                "type": "object",
                "description": (
                    "Env vars for the server process. Use "
                    "for API keys: e.g. {'MINIMAX_API_KEY': 'sk-...'}. "
                    "A blank value clears the key."
                ),
            },
            "headers": {
                "type": "object",
                "description": (
                    "HTTP headers for sse/streamable_http "
                    "servers. A blank value clears the key."
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

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        denied = _gate(context)
        if denied:
            return _err(denied)

        name = (kwargs.get("name") or "").strip()
        if not name:
            return _err("missing required field: name")

        conn_type = (kwargs.get("connection_type") or "").strip().lower()
        if conn_type and conn_type not in ("stdio", "sse", "streamable_http"):
            return _err(
                "connection_type must be one of: stdio, sse, streamable_http"
            )

        # Conditional validation: only run when the field
        # was supplied. The operator might be patching a
        # single field and leaving connection_type unset,
        # in which case we keep the existing transport.
        if conn_type == "stdio" and "command" in kwargs and not kwargs.get("command"):
            return _err("stdio servers require 'command'")
        if conn_type in ("sse", "streamable_http") and "url" in kwargs and not kwargs.get("url"):
            return _err(f"{conn_type} servers require 'url'")

        with open_session() as s:
            row = s.execute(
                select(McpServer).where(McpServer.name == name)
            ).scalar_one_or_none()
            if row is None:
                return _err(
                    f"server '{name}' does not exist. "
                    f"Create it with add_mcp_server first."
                )

            # Apply each field that was supplied. The
            # Pydantic-style ``"key in kwargs"`` check
            # lets the LLM patch a single field by
            # passing just that key.
            if conn_type:
                row.connection_type = conn_type
            if "command" in kwargs:
                row.command = kwargs.get("command")
            if "args" in kwargs:
                row.args_json = json.dumps(kwargs.get("args") or [])
            if "url" in kwargs:
                row.url = kwargs.get("url")
            if "enabled" in kwargs:
                enabled = kwargs.get("enabled")
                if isinstance(enabled, bool):
                    row.enabled = enabled
            if "env" in kwargs:
                row.env_json = json.dumps(kwargs.get("env") or {})
            if "headers" in kwargs:
                row.headers_json = json.dumps(kwargs.get("headers") or {})
            if "connect_timeout" in kwargs:
                row.connect_timeout = kwargs.get("connect_timeout")
            if "execute_timeout" in kwargs:
                row.execute_timeout = kwargs.get("execute_timeout")
            if "sse_read_timeout" in kwargs:
                row.sse_read_timeout = kwargs.get("sse_read_timeout")

            # Final invariant check on the merged row —
            # some combos (e.g. switching transport from
            # stdio to sse without setting ``url``) leave
            # the row in an invalid state.
            if row.connection_type == "stdio" and not (row.command and row.command.strip()):
                return _err(
                    "row is now stdio with no 'command' — "
                    "supply command=... in the same call"
                )
            if row.connection_type in ("sse", "streamable_http") and not (row.url and row.url.strip()):
                return _err(
                    f"row is now {row.connection_type} with no 'url' — "
                    "supply url=... in the same call"
                )

            s.commit()
            logger.info("updated MCP server %s", name)

        return _ok({
            "status": "updated",
            "server": _serialize(row),
            "hint": (
                "Server updated. The new configuration takes "
                "effect on the next chat turn."
            ),
        })
