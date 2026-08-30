"""MCP tool loader — connection + tool-wrapper primitives.

After the :class:`~mcp.worker.McpWorker` refactor, this module
no longer owns the long-lived subprocesses / SSE / streamable-HTTP
connections. It is a library of small classes the worker composes:

- :class:`MCPServerConnection` — the protocol-level handle for one
  server; :meth:`connect` / :meth:`disconnect` open and tear down the
  underlying transport.
- :class:`MCPTool` — a :class:`~tools.BaseTool.BaseTool` wrapper that
  forwards ``run`` calls to ``session.call_tool`` and applies
  ``execute_timeout``.
- :class:`MCPTimeoutConfig` — the three timeout knobs the worker
  reads from ``bus.settings_book``.

The module-level state (``_connections`` cache, ``load_mcp_tools_*``,
``list_tools_for_server``, ``cleanup_mcp_connections``) that
previously lived here has been removed; the
:class:`~mcp.worker.McpWorker` now owns the per-server
connections and re-injects the resulting tools into the
:class:`tools.registry` via
:func:`tools.registry.register_tools`. See
``docs/MCP_WORKER_DESIGN.md`` for the full migration plan.

Subsystem location
------------------

This module still lives in :mod:`mcp`. The agent loop and the
worker reach it directly; the tools package stays agnostic of
MCP. ``MCPServerConnection`` / ``MCPTool`` continue to subclass
:class:`~tools.BaseTool.BaseTool` (the shape every tool in the
registry takes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from tools.BaseTool import BaseTool, ToolResult

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger("mcp.MCPClient")

ConnectionType = Literal["stdio", "sse", "streamable_http"]


@dataclass
class MCPTimeoutConfig:
    """Per-server timeout knobs.

    ``connect_timeout`` caps the upfront handshake with one
    server; ``execute_timeout`` caps the per-tool round-trip;
    ``sse_read_timeout`` is forwarded to ``sse_client`` /
    ``streamablehttp_client`` so a wedged SSE/streamable-HTTP
    peer can't stall the agent loop forever.

    The worker fills these from
    ``bus.settings_book.get_value("mcp.{connect,execute,sse_read}_timeout")``
    at bootstrap time. When the value is unset or fails to
    parse, the worker falls back to the defaults below.
    """

    connect_timeout: float = 10.0
    execute_timeout: float = 60.0
    sse_read_timeout: float = 120.0


# ────────────────────────────────────────────────────────────────── #
# Tool wrapper — adapts an MCP tool to our :class:`BaseTool` protocol.
# ────────────────────────────────────────────────────────────────── #


class MCPTool(BaseTool):
    """One tool surface from an MCP server, wrapped in the :class:`BaseTool` protocol.

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

    MCP tools are intentionally unrestricted by role
    (``ALLOWED_ROLES`` left as the default empty frozenset).
    The MCP server operator decides which tools to expose;
    tightening comes later via per-tool ``allowed_roles``
    frontmatter on the server config side.
    """

    def __init__(
        self,
        *,
        server_name: str,
        server_tool_name: str,
        description: str,
        parameters: dict[str, Any],
        session: ClientSession,
        execute_timeout: float,
    ) -> None:
        # ``name`` is what the LLM invokes; built once in __init__
        # so the registry cache is stable.
        self.name = f"{server_name}__{server_tool_name}"
        self._server_tool_name = server_tool_name
        self.description = description or "(no description provided by MCP server)"
        self._parameters = parameters
        # Anthropic-shaped JSON Schema. The MCP ``inputSchema``
        # is already (close enough to) JSON Schema, so we hand
        # it through verbatim.
        self.input_schema: dict[str, Any] = (
            parameters if parameters else {"type": "object", "properties": {}}
        )
        self._session = session
        self._execute_timeout = execute_timeout

    async def run(self, **kwargs: Any) -> ToolResult:
        """Forward the call to the MCP server."""
        try:
            async with asyncio.timeout(self._execute_timeout):
                result = await self._session.call_tool(self._server_tool_name, arguments=kwargs)
        except TimeoutError:
            server = self.name.split("__", 1)[0]
            return ToolResult(
                content=(
                    f"MCP tool '{self._server_tool_name}' (server {server!r}) "
                    f"timed out after {self._execute_timeout}s. The remote "
                    f"server may be slow or unresponsive — retry later or "
                    f"check the server's logs."
                ),
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"MCP tool '{self._server_tool_name}' failed: {e}",
                is_error=True,
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

        return ToolResult(
            content=content_str,
            is_error=is_error,
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
    """Connection + cached tool list for one MCP server entry.

    The :class:`~mcp.worker.McpWorker` owns one instance per
    enabled server, calls :meth:`connect` at bootstrap or on a
    change job, and :meth:`disconnect` when the server is removed
    or the worker is torn down.
    """

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
    # ``Any`` rather than ``ClientSession``: the SDK type is only
    # available under ``TYPE_CHECKING`` and ``mcp`` lives in ``.venv``
    # (excluded from Pyright's index + ``useLibraryCodeForTypes=false``),
    # so an unresolved forward-ref here breaks the dataclass's inferred
    # ``__init__`` signature. Runtime behaviour is unchanged — the
    # annotation is a string under ``from __future__ import annotations``.
    session: Any | None = None
    exit_stack: AsyncExitStack | None = None
    tools: list[MCPTool] = field(default_factory=list)

    def _connect_timeout(self, defaults: MCPTimeoutConfig) -> float:
        return (
            self.connect_timeout if self.connect_timeout is not None else defaults.connect_timeout
        )

    def _execute_timeout(self, defaults: MCPTimeoutConfig) -> float:
        return (
            self.execute_timeout if self.execute_timeout is not None else defaults.execute_timeout
        )

    def _sse_read_timeout(self, defaults: MCPTimeoutConfig) -> float:
        return (
            self.sse_read_timeout
            if self.sse_read_timeout is not None
            else defaults.sse_read_timeout
        )

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
                "mcp package not installed; skipping server %r (install with `uv pip install mcp`)",
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
            logger.error("MCP server %r: connect timed out after %.1fs", self.name, ct)
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
        the operator's ``env`` keys override the container's for
        any key the operator sets. See the module docstring
        for the rationale (operator-controlled env, not
        "whatever the container happens to have").

        Callers must have already set ``self.exit_stack`` to a
        fresh :class:`~contextlib.AsyncExitStack` before calling
        this method — see :meth:`connect`.
        """
        assert self.exit_stack is not None, "exit_stack must be set before calling _open_streams"
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


__all__ = [
    "MCPTimeoutConfig",
    "MCPServerConnection",
    "MCPTool",
]
