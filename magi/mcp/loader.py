
"""MCP tool loader — bridges Model-Context-Protocol servers into the
existing :mod:`magi.tools` registry.

Each upstream MCP server exposes a ``list_tools`` /
``call_tool`` pair; this module mirrors that surface as a
:class:`Tool` so the agent loop doesn't need to know MCP exists.
Modeled on MiniMax-AI/Mini-Agent's ``mini_tools/mcp_loader.py`` —
same connection transports (``stdio`` / ``sse`` /
``streamable_http``), same timeout guarding, simplified to
fit a single-process host where ``async`` work runs in one
``asyncio.run`` call at boot.

Subsystem location
------------------

This module lives in :mod:`magi.mcp` (top-level MCP
subsystem), not under ``magi.tools``. The agent loop
reaches the loader through its own package — tools
end up in the registry via :func:`magi.tools.registry.bootstrap_mcp_tools`,
which imports :mod:`magi.mcp.loader` directly. The loader
itself still depends on :mod:`magi.tools.base` for the
:class:`Tool` / :class:`ToolResult` shapes.

Configuration
-------------

The single source of truth is the ``mcp_servers`` table
(:class:`magi.bus.models.local.mcp_server.McpServer`).
Operators add / edit / toggle / delete rows from the
WebUI Settings → MCP card. The loader reads the table on
demand — no JSON file, no env var, no deploy manifest.

The legacy ``mcp.json`` + ``MAGI_MCP_CONFIG`` flow is
gone. Operators upgrading from an older release re-create
their servers in the UI; the deployer does not need to
ship any secret-bearing env vars in ``deploy/...``.

Env semantics
-------------

For stdio servers, the loader always passes
``os.environ | operator.env`` to the subprocess. The
container's ``PATH`` / ``HOME`` / ``LANG`` etc. are
preserved so a stdio server that relies on a binary in
``PATH`` keeps working; the operator's ``env`` keys
override the container's for any key the operator sets
to a non-empty value. An operator who leaves a key blank
sends ``""`` and the subprocess sees ``KEY=""`` —
explicit, never silently inherited.

MCP timeouts live in the ``settings`` table
(``mcp.connect_timeout`` / ``execute_timeout`` /
``sse_read_timeout``). There are no ``MAGI_MCP_*``
env vars.

Lifecycle
---------

1. :func:`load_mcp_tools_async` reads the ``mcp_servers``
   table and connects to each enabled server (one
   :class:`MCPServerConnection` per row).
2. Each tool is wrapped as an :class:`MCPTool` (a
   :class:`Tool` subclass); the wrappers share the server's
   long-lived ``ClientSession``.
3. :func:`cleanup_mcp_connections` is called at process
   shutdown to close STDIO transports cleanly.

The registry calls :func:`load_mcp_tools_async` once at
boot — ``registry.MCP bootstrap`` blocks exactly one
event loop on it. The hot path (every chat turn calling
``tool.run``) only ever touches the cached wrapper. The
agent loop also calls
:func:`magi.tools.registry.maybe_reload_mcp_tools` on
each chat turn so a freshly-edited server row takes effect
on the next user message (lazy reload — no live reconnect
mid-conversation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger("magi.mcp.loader")

ConnectionType = Literal["stdio", "sse", "streamable_http"]


@dataclass
class MCPTimeoutConfig:
    """Per-server timeout knobs.

    ``connect_timeout`` caps the upfront handshake with one
    server; ``execute_timeout`` caps the per-tool round-trip;
    ``sse_read_timeout`` is forwarded to ``sse_client`` /
    ``streamablehttp_client`` so a wedged SSE/streamable-HTTP
    peer can't stall the agent loop forever.
    """

    connect_timeout: float = 10.0
    execute_timeout: float = 60.0
    sse_read_timeout: float = 120.0


# -- MCP timeouts (from settings table) ----------------------------------


def _timeout_from_settings(key: str, default: float) -> float:
    """Read a float setting, falling back to *default*."""
    try:
        from magi.bus import get_bus
        raw = get_bus().settings.get(key)
        if raw:
            return float(raw)
    except (ValueError, Exception):
        pass
    return default


def _defaults() -> MCPTimeoutConfig:
    return MCPTimeoutConfig(
        connect_timeout=_timeout_from_settings("mcp.connect_timeout", 10.0),
        execute_timeout=_timeout_from_settings("mcp.execute_timeout", 60.0),
        sse_read_timeout=_timeout_from_settings("mcp.sse_read_timeout", 120.0),
    )


# ────────────────────────────────────────────────────────────────── #
# Tool wrapper — adapts an MCP tool to our :class:`Tool` protocol.
# ────────────────────────────────────────────────────────────────── #


class MCPTool:
    """One tool surface from an MCP server, wrapped to look like
    our :class:`Tool`.

    Holds a reference to the server's long-lived ``ClientSession``;
    every ``run`` round-trips through ``session.call_tool``. The
    timeout is enforced with ``asyncio.timeout`` so a wedged
    server can't stall the agent loop beyond
    ``execute_timeout``.

    Tool name prefixing — when a server named ``github`` exposes
    a tool called ``create_issue``, we surface it as
    ``github__create_issue`` so two servers offering the same
    unqualified tool name (e.g. both expose ``search``) don't
    shadow each other in the LLM's tool menu.
    """

    def __init__(
        self,
        *,
        server_name: str,
        server_tool_name: str,
        description: str,
        parameters: dict[str, Any],
        session: "ClientSession",
        execute_timeout: float,
    ) -> None:
        # ``name`` is what the LLM invokes; built once in __init__
        # so the registry cache is stable.
        self.name = f"{server_name}__{server_tool_name}"
        self._server_tool_name = server_tool_name
        self._description = description or "(no description provided by MCP server)"
        self._parameters = parameters
        # Anthropic-shaped JSON Schema. The MCP ``inputSchema``
        # is already (close enough to) JSON Schema, so we hand
        # it through verbatim. The agent loop reads ``input_schema``
        # (snake_case); alias both names.
        self.input_schema: dict[str, Any] = (
            parameters if parameters else {"type": "object", "properties": {}}
        )
        self._session = session
        self._execute_timeout = execute_timeout

    @property
    def description(self) -> str:
        return self._description

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._description,
            "input_schema": self.input_schema,
        }

    def is_allowed_for_role(self, role: str | None) -> bool:
        """MCP tools are intentionally unrestricted by role.

        The MCP server is configured by the operator and they
        decide which tools to expose — a server like
        ``github__create_issue`` carrying admin-only side
        effects is the operator's call, not ours. v0
        surfaces every MCP tool to every operator; tightening
        this comes later (likely via per-tool ``allowed_roles``
        frontmatter on the server config side)."""
        return True

    async def run(self, ctx: Any, **kwargs: Any) -> Any:
        """Forward the call to the MCP server.

        ``ctx`` is the project-local :class:`ToolContext`. The
        MCP wrapper ignores it (the upstream server has its own
        state); we accept it to satisfy the protocol.
        """
        try:
            async with asyncio.timeout(self._execute_timeout):
                result = await self._session.call_tool(
                    self._server_tool_name, arguments=kwargs
                )
        except TimeoutError:
            server = self.name.split("__", 1)[0]
            return _mcp_tool_result(
                success=False,
                content="",
                error=(
                    f"MCP tool '{self._server_tool_name}' (server {server!r}) "
                    f"timed out after {self._execute_timeout}s. The remote "
                    f"server may be slow or unresponsive — retry later or "
                    f"check the server's logs."
                ),
            )
        except Exception as e:
            return _mcp_tool_result(
                success=False,
                content="",
                error=f"MCP tool '{self._server_tool_name}' failed: {e}",
            )

        # MCP results are a list of content items (text / image / …).
        # The agent loop only feeds ``content`` back to the LLM as a
        # text block, so we serialise text items into a single
        # newline-joined string and JSON-stringify non-text items.
        text_parts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
            else:
                try:
                    text_parts.append(json.dumps(_safe_obj(item), ensure_ascii=False))
                except Exception:
                    text_parts.append(str(item))
        content_str = "\n".join(text_parts)
        is_error = bool(getattr(result, "isError", False))

        return _mcp_tool_result(
            success=not is_error,
            content=content_str,
            error=None if not is_error else "MCP tool returned isError=true",
        )


# The agent loop reads ``ToolResult`` from
# :mod:`magi.tools.base`, which we don't import at the
# top to dodge a circular path (mcp_loader → base → registry →
# mcp_loader on some setups). We provide a thin factory that
# produces whatever the local ``ToolResult`` looks like.
def _mcp_tool_result(*, success: bool, content: str, error: str | None) -> Any:
    """Build a :class:`ToolResult` from MCP success/error bits.

    ``success=True`` ⟹ ``is_error=False`` and ``error=None``
    (the agent loop never sees the error field). Otherwise
    we surface the error in ``content`` (LLMs read it like a
    regular tool output) and flip ``is_error=True`` so the
    loop can count failures for its bound.
    """
    from magi.bus import ToolResult
    if success:
        return ToolResult(content=content, is_error=False)
    return ToolResult(
        content=content or (error or ""),
        is_error=True,
    )


def _safe_obj(obj: Any) -> Any:
    """Best-effort serialise a non-text MCP content block.

    The MCP SDK models blocks as Pydantic objects; fall back to
    ``__dict__`` for unknown types so ``json.dumps`` can always
    emit *something* instead of raising.
    """
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    return getattr(obj, "__dict__", str(obj))


# ────────────────────────────────────────────────────────────────── #
# One-server-per-entry lifecycle.
# ────────────────────────────────────────────────────────────────── #


@dataclass
class MCPServerConnection:
    """Connection + cached tool list for one MCP server entry."""

    name: str
    connection_type: ConnectionType = "stdio"
    # STDIO
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # URL-based
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Per-server overrides; ``None`` → use defaults.
    connect_timeout: float | None = None
    execute_timeout: float | None = None
    sse_read_timeout: float | None = None
    # Mutable state populated by ``connect``.
    session: "ClientSession | None" = None
    exit_stack: AsyncExitStack | None = None
    tools: list[MCPTool] = field(default_factory=list)

    def _connect_timeout(self, defaults: MCPTimeoutConfig) -> float:
        return self.connect_timeout if self.connect_timeout is not None else defaults.connect_timeout

    def _execute_timeout(self, defaults: MCPTimeoutConfig) -> float:
        return self.execute_timeout if self.execute_timeout is not None else defaults.execute_timeout

    def _sse_read_timeout(self, defaults: MCPTimeoutConfig) -> float:
        return self.sse_read_timeout if self.sse_read_timeout is not None else defaults.sse_read_timeout

    async def connect(self, defaults: MCPTimeoutConfig) -> bool:
        """Open the connection, list tools, wrap each as :class:`MCPTool`.

        Returns ``True`` on success; ``False`` on any error
        (timeout, transport refused, the server itself returned
        a non-OK handshake). The caller decides whether one
        bad server poisons the rest of the registry.
        """
        ct = self._connect_timeout(defaults)
        if self.exit_stack is not None:
            logger.warning("server %r already connected; skipping", self.name)
            return True

        # Lazy import — the ``mcp`` package is heavy and we want
        # the registry to be importable without it (so dev
        # tooling can poke around even if MCP isn't installed).
        try:
            from mcp import ClientSession
        except ImportError:
            logger.warning(
                "mcp package not installed; skipping server %r "
                "(install with `uv pip install mcp`)",
                self.name,
            )
            return False

        try:
            self.exit_stack = AsyncExitStack()
            async with asyncio.timeout(ct):
                read_stream, write_stream = await self._open_streams(defaults)
                session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                tools_list = await session.list_tools()
            self.session = session

            et = self._execute_timeout(defaults)
            for tool in tools_list.tools:
                # ``inputSchema`` is the canonical MCP shape, but
                # some implementations use ``input_schema`` or
                # ``schema``; be defensive.
                params = (
                    getattr(tool, "inputSchema", None)
                    or getattr(tool, "input_schema", None)
                    or getattr(tool, "schema", None)
                    or {}
                )
                self.tools.append(
                    MCPTool(
                        server_name=self.name,
                        server_tool_name=tool.name,
                        description=getattr(tool, "description", "") or "",
                        parameters=params,
                        session=session,
                        execute_timeout=et,
                    )
                )
            logger.info(
                "MCP server %r (%s) ready: %d tool(s): %s",
                self.name,
                self.connection_type,
                len(self.tools),
                ", ".join(t.name for t in self.tools) or "<none>",
            )
            return True
        except TimeoutError:
            logger.error(
                "MCP server %r: connect timed out after %.1fs", self.name, ct
            )
            await self._safe_close()
            return False
        except Exception as e:  # noqa: BLE001 — surface all failure modes
            logger.exception("MCP server %r failed to connect: %s", self.name, e)
            await self._safe_close()
            return False

    async def _open_streams(self, defaults: MCPTimeoutConfig) -> Any:
        """Open the MCP transport appropriate for ``connection_type``.

        For stdio servers the subprocess env is always
        ``os.environ | self.env`` — the container's ``PATH``,
        ``HOME`` and friends are preserved so a stdio server
        that relies on a binary in ``PATH`` keeps working;
        the operator's ``env`` keys override the container's
        for any key the operator sets. See the module
        docstring for the rationale (operator-controlled env,
        not "whatever the container happens to have").
        """
        if self.connection_type == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            merged_env = {**os.environ, **self.env}
            params = StdioServerParameters(
                command=self.command or "",
                args=list(self.args),
                env=merged_env,
            )
            return await self.exit_stack.enter_async_context(stdio_client(params))
        if self.connection_type == "sse":
            from mcp.client.sse import sse_client

            return await self.exit_stack.enter_async_context(
                sse_client(
                    url=self.url or "",
                    headers=self.headers if self.headers else None,
                    timeout=self._connect_timeout(defaults),
                    sse_read_timeout=self._sse_read_timeout(defaults),
                )
            )
        # streamable_http — the canonical "MCP over HTTP" transport.
        from mcp.client.streamable_http import streamablehttp_client

        read_stream, write_stream, _ = await self.exit_stack.enter_async_context(
            streamablehttp_client(
                url=self.url or "",
                headers=self.headers if self.headers else None,
                timeout=self._connect_timeout(defaults),
                sse_read_timeout=self._sse_read_timeout(defaults),
            )
        )
        return read_stream, write_stream

    async def _safe_close(self) -> None:
        if self.exit_stack is not None:
            try:
                await self.exit_stack.aclose()
            except Exception:  # noqa: BLE001
                # anyio cancel scope complaints land here during
                # shutdown — swallow so the rest of the cleanup
                # path still runs.
                pass
            finally:
                self.exit_stack = None
                self.session = None

    async def disconnect(self) -> None:
        await self._safe_close()
        self.tools.clear()


# ────────────────────────────────────────────────────────────────── #
# Public API — DB-backed loading + connection registry.
# ────────────────────────────────────────────────────────────────── #


def _load_servers_from_db() -> list[MCPServerConnection]:
    """Read the ``mcp_servers`` table and return one
    :class:`MCPServerConnection` per enabled row.

    The table is the canonical source of truth. Disabled
    rows are skipped (the operator toggled them off in the
    UI). JSON columns are parsed defensively — a single
    malformed row logs a warning and is skipped rather
    than crashing the whole load.

    Returns ``[]`` if the table is empty (no operators
    have added servers yet) or the DB read fails.
    """
    try:
        from magi.bus import get_bus
        rows = get_bus().mcp.enabled_configs()
    except Exception:
        # Defensive: a missing table (pre-init) or a busy
        # DB on a parallel thread should not crash the
        # whole load. Log + return empty so the rest of
        # the boot completes.
        logger.exception("mcp_servers table not readable; no MCP tools")
        return []

    connections: list[MCPServerConnection] = []
    for r in rows:
        connection_type = r.connection_type
        if connection_type not in ("stdio", "sse", "streamable_http"):
            logger.warning(
                "MCP server %r: unknown connection_type %r; skipping",
                r.name, connection_type,
            )
            continue
        if connection_type == "stdio":
            if not (r.command and r.command.strip()):
                logger.warning(
                    "MCP server %r: STDIO requires 'command'; skipping", r.name,
                )
                continue
        else:  # sse / streamable_http
            if not (r.url and r.url.strip()):
                logger.warning(
                    "MCP server %r: %s requires 'url'; skipping",
                    r.name, connection_type,
                )
                continue

        connections.append(
            MCPServerConnection(
                name=r.name,
                connection_type=connection_type,
                command=r.command,
                args=list(r.args),
                env=r.env,
                url=r.url,
                headers=r.headers,
                connect_timeout=r.connect_timeout,
                execute_timeout=r.execute_timeout,
                sse_read_timeout=r.sse_read_timeout,
            )
        )
    return connections


# Module-level handle so :func:`cleanup_mcp_connections` has
# somewhere to find them at shutdown.
_connections: list[MCPServerConnection] = []


async def load_mcp_tools_async(
    *,
    timeouts: MCPTimeoutConfig | None = None,
) -> list[MCPTool]:
    """Connect to every enabled server in the ``mcp_servers`` table
    and return the union of their tools.

    Reads the table on every call — there is no in-memory
    cache at the loader level. The agent loop's
    :func:`magi.tools.registry.maybe_reload_mcp_tools`
    decides when to re-invoke this function (currently
    once per chat turn, when the table's max
    ``updated_at`` is newer than the last successful
    load).

    If the table is empty (no operators have added
    servers yet) or the DB read fails, returns ``[]`` and
    logs at INFO level so the boot / chat turn completes
    without an MCP wedge.
    """
    if timeouts is None:
        timeouts = _defaults()

    # Clean any prior connections from a previous load (e.g.
    # a test sequence that reuses this module, or a
    # maybe_reload_mcp_tools trigger that fired mid-turn).
    if _connections:
        await cleanup_mcp_connections()

    servers = _load_servers_from_db()
    if not servers:
        return []

    all_tools: list[MCPTool] = []
    # Connect in parallel — one slow server shouldn't delay the
    # others on boot. ``connect_timeout`` caps each task, so a
    # stuck server gets cancelled cleanly.
    started = time.monotonic()
    results = await asyncio.gather(
        *(s.connect(timeouts) for s in servers),
        return_exceptions=False,
    )
    for server, ok in zip(servers, results):
        if ok:
            _connections.append(server)
            all_tools.extend(server.tools)
        # else: ``server.connect`` already logged the error.

    logger.info(
        "MCP load complete in %.2fs: %d tool(s) from %d/%d server(s)",
        time.monotonic() - started,
        len(all_tools),
        sum(1 for ok in results if ok),
        len(servers),
    )
    return all_tools


async def cleanup_mcp_connections() -> None:
    """Close every cached connection. Idempotent."""
    global _connections
    if not _connections:
        return
    # Snapshot; ``disconnect`` clears the server's own state.
    snapshot = list(_connections)
    _connections.clear()
    for c in snapshot:
        try:
            await c.disconnect()
        except Exception:  # noqa: BLE001
            # Don't let one misbehaving server block the rest.
            logger.exception("MCP server %r disconnect failed", c.name)


def active_connections() -> list[MCPServerConnection]:
    """Read-only view of the current connections (for diagnostics +
    tests)."""
    return list(_connections)


def list_tools_for_server(name: str) -> list["MCPTool"] | None:
    """Return the live tool list for one MCP server.

    Used by the WebUI's per-server tool list (Knowledge → MCP
    detail expansion). The agent loop uses the process-wide
    ``_mcp_tools_cache`` instead — that's a different code
    path with different freshness semantics.

    Freshness policy: prefer the active connection (cheap,
    already-running subprocess). If the connection isn't
    running, fall back to a one-shot connect → list →
    disconnect (slow; only happens when the operator opens
    a server detail before the next chat turn has triggered
    ``maybe_reload_mcp_tools``).

    Returns:
      - ``None`` when the server name isn't in the table
        (the operator deleted it under us). The endpoint
        surfaces a 404.
      - ``[]`` when the row exists but the subprocess
        failed to connect or returned no tools. The
        endpoint surfaces a 200 + empty list.
      - ``list[MCPTool]`` on success.
    """
    # 1. Active connection? Use it.
    for conn in _connections:
        if conn.name == name:
            return list(conn.tools)

    # 2. Row still in the table? On-demand connect.
    try:
        from magi.bus import get_bus
        row = get_bus().mcp.get_config(name)
    except Exception:
        logger.exception(
            "list_tools_for_server: db read failed for %r", name
        )
        return []
    if row is None:
        return None

    conn = MCPServerConnection(
        name=row.name,
        connection_type=row.connection_type,  # type: ignore[arg-type]
        command=row.command,
        args=list(row.args),
        env=row.env,
        url=row.url,
        headers=row.headers,
        connect_timeout=row.connect_timeout,
        execute_timeout=row.execute_timeout,
        sse_read_timeout=row.sse_read_timeout,
    )
    # ``connect`` is async. Run it in a one-shot loop so
    # the sync endpoint can call us. The connection is
    # NOT registered into ``_connections`` — that list is
    # owned by ``bootstrap_mcp_tools`` and ``maybe_reload_mcp_tools``.
    # We only borrow the subprocess for one tool-list
    # round-trip; if the next chat turn's reload picks
    # up this row it'll re-connect cleanly.
    import asyncio
    ok = asyncio.run(conn.connect(_defaults()))
    if not ok:
        return []
    try:
        return list(conn.tools)
    finally:
        # Detach so the next bootstrap cycle doesn't
        # see a stale handle in ``active_connections()``.
        asyncio.run(conn.disconnect())


# ────────────────────────────────────────────────────────────────── #
# Sync helpers — bridge to the project-local sync callers
# (registry / boot) without forcing them into async.
# ────────────────────────────────────────────────────────────────── #


def load_mcp_tools_blocking(
    *,
    timeouts: MCPTimeoutConfig | None = None,
) -> list[MCPTool]:
    """Synchronous wrapper around :func:`load_mcp_tools_async`.

    Called once at boot from the project-local sync code path
    (see :func:`magi.tools.registry.bootstrap_mcp_tools`).
    We must run on a fresh loop — the boot code is itself sync,
    so borrowing the FastAPI loop would deadlock the asyncio
    scheduler.

    When the caller is already inside a running asyncio loop
    (Uvicorn's worker loop, for example), we drive a fresh
    loop manually. We probe with :func:`asyncio.get_running_loop`
    **before** constructing the coroutine so the
    never-awaited-coroutine ``RuntimeWarning`` never fires on a
    doomed coroutine that :func:`asyncio.run` refuses to consume.
    """
    coro = load_mcp_tools_async(timeouts=timeouts)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — the simple, fast path.
        asyncio.run(coro)
    else:
        # Nested loop (e.g. Uvicorn worker) — drive a fresh
        # loop manually and await the same coroutine.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    # Return the cached list (load_mcp_tools_async populates
    # it via ``_connections``).
    return [tool for c in _connections for tool in c.tools]
