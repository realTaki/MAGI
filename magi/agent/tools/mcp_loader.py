"""MCP tool loader — bridges Model-Context-Protocol servers into the
existing :mod:`magi.agent.tools` registry.

Each upstream MCP server exposes a ``list_tools`` /
``call_tool`` pair; this module mirrors that surface as a
:class:`Tool` so the agent loop doesn't need to know MCP exists.
Modeled on MiniMax-AI/Mini-Agent's ``mini_agent/tools/mcp_loader.py`` —
same connection transports (``stdio`` / ``sse`` /
``streamable_http``), same timeout guarding, simplified to
fit a single-process host where ``async`` work runs in one
``asyncio.run`` call at boot.

Configuration
-------------

The deployer drops an ``mcp.json`` next to ``memories/`` (or
sets ``MAGI_MCP_CONFIG`` to any other absolute path). Schema::

    {
      "mcpServers": {
        "fetch":  {"command": "uvx", "args": ["mcp-server-fetch"]},
        "github": {
          "url": "https://api.example.com/mcp",
          "type": "streamable_http",
          "headers": {"Authorization": "Bearer …"}
        }
      }
    }

A missing file just means "no MCP tools"; the boot never
fails on this layer. ``mcp-example.json`` ships as a
deployer-starter template (mirrors the upstream convention).

Lifecycle
---------

1. :func:`load_mcp_tools_async` connects to each server (one
   :class:`MCPServerConnection` per entry) and gathers tools.
2. Each tool is wrapped as an :class:`MCPTool` (a
   :class:`Tool` subclass); the wrappers share the server's
   long-lived ``ClientSession``.
3. :func:`cleanup_mcp_connections` is called at process
   shutdown to close STDIO transports cleanly.

The registry calls :func:`load_mcp_tools_async` once at
boot — ``registry.load_mcp_tools_into_registry`` blocks
exactly one event loop on it. The hot path (every chat turn
calling ``tool.run``) only ever touches the cached wrapper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger("magi.agent.tools.mcp_loader")

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


# Default timeouts — overridable via env so a CI harness or a
# deployer can tighten them without touching code.
_ENV_CONNECT = "MAGI_MCP_CONNECT_TIMEOUT"
_ENV_EXEC = "MAGI_MCP_EXECUTE_TIMEOUT"
_ENV_SSE = "MAGI_MCP_SSE_READ_TIMEOUT"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("ignoring bad %s=%r (expected float)", name, raw)
        return default


def _defaults() -> MCPTimeoutConfig:
    return MCPTimeoutConfig(
        connect_timeout=_env_float(_ENV_CONNECT, 10.0),
        execute_timeout=_env_float(_ENV_EXEC, 60.0),
        sse_read_timeout=_env_float(_ENV_SSE, 120.0),
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
                    f"timed out after {self._execute_timeout}s"
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
# :mod:`magi.agent.tools.base`, which we don't import at the
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
    from magi.agent.tools.base import ToolResult
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
        """Open the MCP transport appropriate for ``connection_type``."""
        if self.connection_type == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self.command or "",
                args=list(self.args),
                env=self.env if self.env else None,
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
# Public API — file loading + connection registry.
# ────────────────────────────────────────────────────────────────── #


def resolve_config_path(config_path: str) -> Path | None:
    """Resolve the config path with the ``mcp-example.json``
    fallback.

    Priority:
      1. The path the caller passed in.
      2. If the path didn't exist AND it was ``mcp.json``,
         try ``mcp-example.json`` next to it (deployer-starter).
      3. ``None`` — caller treats this as "no MCP servers
         configured, skip silently".
    """
    p = Path(config_path)
    if p.exists():
        return p
    if p.name == "mcp.json":
        example = p.parent / "mcp-example.json"
        if example.exists():
            logger.info("mcp.json not found; falling back to %s", example)
            return example
    return None


def _determine_connection_type(server_config: dict[str, Any]) -> ConnectionType:
    explicit = str(server_config.get("type", "")).lower()
    if explicit in ("stdio", "sse", "http", "streamable_http"):
        # ``http`` is the legacy alias for ``streamable_http``.
        return "streamable_http" if explicit == "http" else explicit  # type: ignore[return-value]
    url = server_config.get("url")
    if isinstance(url, str) and url.strip():
        return "streamable_http"
    return "stdio"


def _load_servers_from_config(
    config_file: Path,
) -> list[MCPServerConnection]:
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    servers = raw.get("mcpServers", {}) or {}
    if not isinstance(servers, dict):
        logger.warning("mcpServers must be an object; got %s", type(servers).__name__)
        return []

    connections: list[MCPServerConnection] = []
    for server_name, cfg in servers.items():
        if not isinstance(cfg, dict):
            logger.warning("server %r: config must be an object", server_name)
            continue
        if cfg.get("disabled"):
            logger.info("server %r: disabled in config; skipping", server_name)
            continue

        conn_type = _determine_connection_type(cfg)
        if conn_type == "stdio":
            cmd = cfg.get("command")
            if not (isinstance(cmd, str) and cmd.strip()):
                logger.warning("server %r: STDIO requires 'command'", server_name)
                continue
        else:  # sse / streamable_http
            url = cfg.get("url")
            if not (isinstance(url, str) and url.strip()):
                logger.warning("server %r: %s requires 'url'", server_name, conn_type)
                continue

        connections.append(
            MCPServerConnection(
                name=server_name,
                connection_type=conn_type,
                command=cfg.get("command"),
                args=list(cfg.get("args") or []),
                env=dict(cfg.get("env") or {}),
                url=cfg.get("url"),
                headers=dict(cfg.get("headers") or {}),
                connect_timeout=_as_float_or_none(cfg.get("connect_timeout")),
                execute_timeout=_as_float_or_none(cfg.get("execute_timeout")),
                sse_read_timeout=_as_float_or_none(cfg.get("sse_read_timeout")),
            )
        )
    return connections


def _as_float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Module-level handle so :func:`cleanup_mcp_connections` has
# somewhere to find them at shutdown.
_connections: list[MCPServerConnection] = []


async def load_mcp_tools_async(
    config_path: str | os.PathLike[str] | None = None,
    *,
    timeouts: MCPTimeoutConfig | None = None,
) -> list[MCPTool]:
    """Connect to every enabled server listed in the config and
    return the union of their tools.

    ``config_path`` resolution order:
      1. Explicit argument.
      2. ``MAGI_MCP_CONFIG`` env var (typical: absolute path
         like ``/workspace/memories/mcp.json``).
      3. ``./mcp.json`` next to the CWD.

    If nothing resolves, returns ``[]`` and logs at INFO level
    so the boot continues without an MCP wedge.
    """
    if timeouts is None:
        timeouts = _defaults()

    # Resolve config path.
    if config_path is None:
        env = os.environ.get("MAGI_MCP_CONFIG")
        config_path = env if env else "mcp.json"
    cfg_path = resolve_config_path(str(config_path))
    if cfg_path is None:
        logger.info("no MCP config (tried %r); no MCP tools loaded", str(config_path))
        return []

    # Clean any prior connections from a previous load (e.g.
    # a test sequence that reuses this module).
    if _connections:
        await cleanup_mcp_connections()

    servers = _load_servers_from_config(cfg_path)
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


# ────────────────────────────────────────────────────────────────── #
# Sync helpers — bridge to the project-local sync callers
# (registry / boot) without forcing them into async.
# ────────────────────────────────────────────────────────────────── #


def load_mcp_tools_blocking(
    config_path: str | os.PathLike[str] | None = None,
    *,
    timeouts: MCPTimeoutConfig | None = None,
) -> list[MCPTool]:
    """Synchronous wrapper around :func:`load_mcp_tools_async`.

    Called once at boot from the project-local sync code path
    (see :func:`magi.agent.tools.registry.bootstrap_mcp_tools`).
    We must run on a fresh loop — the boot code is itself sync,
    so borrowing the FastAPI loop would deadlock the asyncio
    scheduler.
    """
    try:
        asyncio.run(load_mcp_tools_async(config_path, timeouts=timeouts))
    except RuntimeError as e:
        # Nested loop — fall back to the ``_nest`` pattern.
        if "asyncio.run() cannot be called" not in str(e):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(load_mcp_tools_async(config_path, timeouts=timeouts))
        finally:
            loop.close()
    # Return the cached list (load_mcp_tools_async populates
    # it via ``_connections``).
    return [tool for c in _connections for tool in c.tools]
