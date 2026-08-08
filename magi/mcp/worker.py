"""MCP Worker — durable consumer that owns every MCP server connection.

``McpWorker`` follows the same constructor-injection pattern as
:class:`~magi.providers.worker.ProvidersWorker` and
:class:`~magi.tools.worker.ToolsWorker`:

- **Only depends on new_bus**. The composition root (see
  :mod:`magi.startup.runtime`) wires a :class:`~magi.new_bus.NewBus`
  with a ready-to-use :class:`mcpServerChangedJobBoard` and
  :class:`~magi.new_bus.library.local.mcpServerBook.McpServerBook`.
- **No environment reads**. Timeouts and per-server config come
  from ``bus.settings_book`` / the row, never from ``os.environ``.
- **No env-knob concurrency** — the worker is the only one, so
  there is no slot semaphore to parameterise.

Lifecycle
---------

::

    start()
      ├─ register_tools("mcp_manage", [...CRUD tools...])
      ├─ await _bootstrap_connections()           # parallel connect of every enabled row
      │   └─ register_tools("mcp", [t for c in _connections for t in c.tools])
      │        └─ on_tools_changed → ToolsWorker auto-republishes catalog
      └─ spawn _run() task (claims mcpServerChangedJobBoard)
    stop()
      ├─ cancel _run() task
      ├─ _disconnect_all()
      └─ register_tools("mcp", [])               # ToolsWorker re-publishes empty MCP

Design points
------------

- **Job Board consumption** — the worker is the only claimer of
  :class:`mcpServerChangedJobBoard`. Today the WebUI / LLM manage
  tools still write to the old bus
  :class:`magi.bus.jobs.services.mcp.McpService`; nothing publishes
  ``McpServerChangedJob`` yet (TODO markers in
  ``magi/mcp/manage.py`` and ``magi/channels/api/mcp_servers.py``).
  Worker therefore idles on ``claim()`` and only acts on bootstrap
  (full table read) and shutdown.

- **Tool sources** — discovered tools register under
  ``"mcp"``; CRUD tools under ``"mcp_manage"``. The ToolsWorker
  already re-publishes on any source change; we do not poke the
  catalog ourselves.

- **Failure isolation** — one bad server at bootstrap logs an
  error and is skipped. ``_run`` swallows ``claim`` /
  ``submit_result`` exceptions so a transient DB blip cannot crash
  the worker loop.

- **Timeouts** — read from ``bus.settings_book`` at
  :meth:`_timeouts_from_bus` time. Settings defaults
  (``mcp.connect_timeout`` / ``mcp.execute_timeout`` /
  ``mcp.sse_read_timeout``) fall back to the loader's previous
  constants (10 / 60 / 120 seconds).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from magi.mcp.manage import (
    AddMcpServerTool,
    DeleteMcpServerTool,
    ListMcpServersTool,
    UpdateMcpServerTool,
)
from magi.new_bus.guild import (
    McpServerChangedJob,
    McpServerChangedResult,
)
from magi.tools.registry import register_tools

if TYPE_CHECKING:
    from magi.mcp.loader import MCPServerConnection, MCPTimeoutConfig
    from magi.new_bus import NewBus

logger = logging.getLogger("magi.mcp.worker")

#: Default per-server timeouts when the operator hasn't set
#: ``mcp.*_timeout`` in the settings book. Mirrors the values
#: the previous ``magi.mcp.loader`` defaults used.
_DEFAULT_CONNECT_TIMEOUT = 10.0
_DEFAULT_EXECUTE_TIMEOUT = 60.0
_DEFAULT_SSE_READ_TIMEOUT = 120.0

#: Module-level singleton — composition root drives the lifecycle.
_worker: McpWorker | None = None


class McpWorker:
    """Consumer that owns every MCP server connection in a MAGI process.

    Receives a fully-wired :class:`~magi.new_bus.NewBus` via
    constructor injection. The :class:`mcpServerChangedJobBoard` is
    drained in the background; :meth:`_bootstrap_connections`
    reads the current enabled set on startup.
    """

    def __init__(
        self,
        bus: NewBus,
        *,
        poll_seconds: float = 0.25,
    ) -> None:
        self.bus = bus
        self.poll_seconds = poll_seconds
        self._connections: dict[str, MCPServerConnection] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        # 1. Always expose the four CRUD tools (admin role gate
        #    lives on each Tool subclass). Without this the
        #    operator cannot add their first MCP server from the
        #    LLM menu even if zero rows exist.
        register_tools("mcp_manage", [
            AddMcpServerTool(),
            ListMcpServersTool(),
            UpdateMcpServerTool(),
            DeleteMcpServerTool(),
        ])

        # 2. Connect every currently-enabled row in parallel.
        await self._bootstrap_connections()

        # 3. Drain change jobs forever (current code path: empty
        #    board — see module docstring).
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="magi-mcp-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._disconnect_all()
        # Clear the source — the ToolsWorker listener will see
        # the empty list and republish the catalog without the
        # MCP tools on the next iteration.
        register_tools("mcp", [])

    # -- claim loop -------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.mcp_server_changed_job_board.claim
                )
            except Exception:
                logger.exception("mcp worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._handle_change(job)

    # -- startup / per-change handling -----------------------------------

    async def _bootstrap_connections(self) -> None:
        """Connect every enabled row in parallel; aggregate tools.

        A single bad server is logged and skipped — never raises
        out of bootstrap. After all attempts complete the
        accumulated tool list is re-injected via
        :func:`register_tools` (triggers the ToolsWorker listener).
        """
        try:
            servers = self.bus.mcp_servers_book.list_enabled()
        except Exception:
            logger.exception("mcp worker: mcp_servers_book.list_enabled failed")
            register_tools("mcp", [])
            return

        if not servers:
            register_tools("mcp", [])
            logger.info("mcp worker: bootstrapped 0/0 servers (none enabled)")
            return

        timeouts = self._timeouts_from_bus()
        connected: dict[str, MCPServerConnection] = {}

        async def _connect_one(server: Any) -> tuple[str, MCPServerConnection | None]:
            conn = self._build_connection(server, timeouts)
            ok = await conn.connect(timeouts)
            return (server.name, conn if ok else None)

        results = await asyncio.gather(
            *(_connect_one(srv) for srv in servers),
            return_exceptions=False,
        )
        for name, conn in results:
            if conn is not None:
                connected[name] = conn
        self._connections = connected
        self._reinject_tools()
        logger.info(
            "mcp worker: bootstrapped %d/%d servers",
            len(connected),
            len(servers),
        )

    async def _handle_change(self, job: McpServerChangedJob) -> None:
        """Route one ``McpServerChangedJob`` to the right helper.

        Always :meth:`submit_result` so the Job Board reaches a
        terminal state — even unknown ``kind`` values report a
        failure back instead of leaking the row as ``processing``.
        """
        name = job.server_name
        success = False
        error: str | None = None
        try:
            if job.kind == "deleted":
                await self._remove_server(name)
                success = True
            elif job.kind in ("added", "updated", "toggled"):
                await self._reload_server(name)
                success = True
            else:
                error = f"unknown change kind: {job.kind!r}"
        except Exception as exc:  # noqa: BLE001 — surface every failure
            logger.exception(
                "mcp worker: failed to handle change for %r (kind=%s)",
                name,
                job.kind,
            )
            error = str(exc)

        try:
            self.bus.mcp_server_changed_job_board.submit_result(
                key=job.job_id,
                result=McpServerChangedResult(
                    job_id=job.job_id,
                    success=success,
                    error=error,
                ),
            )
        except Exception:
            logger.exception(
                "mcp worker: failed to submit result for %s", job.job_id
            )

    async def _remove_server(self, name: str) -> None:
        existing = self._connections.pop(name, None)
        if existing is not None:
            await existing.disconnect()
        self._reinject_tools()

    async def _reload_server(self, name: str) -> None:
        """Reload a single server: drop, then re-read the row +
        re-connect. ``added``/``updated``/``toggled`` all share
        this path because the effect is "the row is now whatever
        the Book says, reconnect accordingly"."""
        existing = self._connections.pop(name, None)
        if existing is not None:
            await existing.disconnect()

        try:
            row = self.bus.mcp_servers_book.get_by_name(name=name)
        except Exception:
            logger.exception(
                "mcp worker: mcp_servers_book.get_by_name failed for %r", name
            )
            row = None
        # Missing row or disabled row → leave it out of the
        # connection map; the next ``register_tools("mcp", ...)``
        # reflects the absence.
        if row is None or not row.enabled:
            self._reinject_tools()
            return

        timeouts = self._timeouts_from_bus()
        conn = self._build_connection(row, timeouts)
        if await conn.connect(timeouts):
            self._connections[name] = conn
        self._reinject_tools()

    async def _disconnect_all(self) -> None:
        if not self._connections:
            return
        snapshot = list(self._connections.values())
        self._connections.clear()
        for conn in snapshot:
            try:
                await conn.disconnect()
            except Exception:
                logger.exception("mcp worker: disconnect failed for %r", conn.name)

    # -- helpers ---------------------------------------------------------

    def _build_connection(
        self,
        server: Any,
        timeouts: MCPTimeoutConfig,
    ) -> MCPServerConnection:
        """Wrap a DTO row in a fresh :class:`MCPServerConnection`.

        *timeouts* is accepted to match the test patch surface and
        to keep room for future per-server timeouts that come from
        the bus (today the row's own ``connect_timeout`` /
        ``execute_timeout`` / ``sse_read_timeout`` win; the
        :class:`MCPServerConnection` falls back to *timeouts* when
        the row leaves a slot blank). Imported lazily so the
        registry / startup code paths that import
        :class:`McpWorker` don't drag the ``mcp`` SDK at import time.
        """
        # Silence ARG002 while keeping the parameter — tests
        # monkeypatch this method and rely on the two-arg shape.
        del timeouts
        from magi.mcp.loader import MCPServerConnection

        return MCPServerConnection(
            name=server.name,
            connection_type=server.connection_type,  # type: ignore[arg-type]
            command=server.command,
            args=list(server.args),
            env=dict(server.env),
            url=server.url,
            headers=dict(server.headers),
            connect_timeout=server.connect_timeout,
            execute_timeout=server.execute_timeout,
            sse_read_timeout=server.sse_read_timeout,
        )

    def _timeouts_from_bus(self) -> MCPTimeoutConfig:
        """Read the three MCP timeouts from the settings book.

        ``None`` on read error or unset value; the connection
        falls back to the loader's defaults.
        """
        from magi.mcp.loader import MCPTimeoutConfig

        def _read(key: str, default: float) -> float:
            try:
                raw = self.bus.settings_book.get(key=key)
            except Exception:
                return default
            if not raw:
                return default
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return default
            return value

        return MCPTimeoutConfig(
            connect_timeout=_read(
                "mcp.connect_timeout", _DEFAULT_CONNECT_TIMEOUT
            ),
            execute_timeout=_read(
                "mcp.execute_timeout", _DEFAULT_EXECUTE_TIMEOUT
            ),
            sse_read_timeout=_read(
                "mcp.sse_read_timeout", _DEFAULT_SSE_READ_TIMEOUT
            ),
        )

    def _reinject_tools(self) -> None:
        """Aggregate tools from every live connection + republish.

        Fires the ``on_tools_changed`` listener registered by
        :class:`~magi.tools.worker.ToolsWorker` — the worker
        observes the dirty flag on its next iteration and
        republishes the catalog.
        """
        all_tools: list[Any] = [
            tool for conn in self._connections.values() for tool in conn.tools
        ]
        register_tools("mcp", all_tools)

    # -- read-only view (for tests / future diagnostics) -----------------

    def connections_view(self) -> dict[str, MCPServerConnection]:
        """Return a shallow copy of the current connection map.

        Tests use this to assert ``McpWorker`` state without
        reaching into private attributes. Production code
        reaches the live connections through this method too
        if it ever needs to inspect them.
        """
        return dict(self._connections)


# -- module-level singletons -----------------------------------------------


async def start_mcp_worker(bus: NewBus) -> McpWorker:
    """Start the process-local MCP worker.

    The composition root passes the fully-wired
    :class:`~magi.new_bus.NewBus`. Subsequent calls return the
    already-started worker (mirrors the pattern in
    :mod:`magi.tools.worker`).
    """
    global _worker
    if _worker is None:
        _worker = McpWorker(bus=bus)
        await _worker.start()
    return _worker


async def stop_mcp_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


__all__ = ["McpWorker", "start_mcp_worker", "stop_mcp_worker"]
