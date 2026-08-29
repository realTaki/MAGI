"""MCP Worker — sole writer to :class:`McpServerBook`, owner of every MCP connection.

``McpWorker`` follows the same constructor-injection pattern as
:class:`~magi.providers.worker.ProvidersWorker` and
:class:`~magi.tools.worker.ToolsWorker`:

- **Only depends on bus**. The composition root (see
  :mod:`magi.startup.runtime`) wires a :class:`~magi.bus.Bus`
  with a ready-to-use :class:`changeMCPServerJobBoard` and
  :class:`~magi.bus.firmwares.books.local.mcpServerBook.McpServerBook`.
- **No environment reads**. Timeouts and per-server config come
  from ``bus.settings_book`` / the row, never from ``os.environ``.
- **No environment concurrency knob** — bounded in-process concurrency is
  constructor-injected through :class:`~magi.runtime_worker.RuntimeWorker`.

Write authority
---------------

The Worker is the **only writer** to ``McpServerBook``. The LLM
manage tools (under :mod:`magi.tools.mcp`) only publish a
:class:`~magi.bus.firmwares.jobs.changeMCPServerJob.ChangeMCPServerJob`
— they never call ``book.upsert`` / ``book.delete`` /
``book.update`` themselves. The Worker claims the job, applies the
write, and reconnects the live connection in the same handler.
Putting both halves under one transaction boundary keeps the LLM's
view of the world and the Worker's live connections in sync.

Lifecycle
---------

::

    start()
      ├─ _bootstrap_connections()    # full table read + parallel connect
      │   └─ register_tools("mcp", [...discovered tools...])
      │        └─ on_tools_changed → ToolsWorker auto-republishes catalog
      └─ spawn _run() task (claims changeMCPServerJobBoard)
    stop()
      ├─ cancel _run() task
      ├─ _disconnect_all()
      └─ register_tools("mcp", [])  # ToolsWorker re-publishes empty MCP

Tool registration
-----------------

The four CRUD tools (``add_mcp_server`` / ``list_mcp_servers`` /
``update_mcp_server`` / ``delete_mcp_server``) live under
:mod:`magi.tools.mcp` and are registered by the standard builtin
tools path (``magi.tools.registry._build_tools``) — the MCP
worker does **not** import or register them. Discovered tools are
still registered under source ``"mcp"`` here, so the ToolsWorker
listener re-publishes the catalog whenever the connection set
changes.

Failure isolation
-----------------

One bad server at bootstrap logs an error and is skipped.
``_run`` swallows ``claim`` / ``submit_result`` exceptions so a
transient DB blip cannot crash the worker loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from magi.old_bus.bases.job import JobStatus
from magi.old_bus.firmwares.jobs import (
    ChangeMCPServerJob,
    ChangeMCPServerResult,
    MCPKind,
)
from magi.runtime_worker import RuntimeWorker
from magi.tools.registry import register_tools

if TYPE_CHECKING:
    from magi.old_bus import Bus
    from magi.old_bus.firmwares.books.local.mcpServerBook import McpServer
    from magi.mcp.MCPClient import MCPServerConnection, MCPTimeoutConfig

logger = logging.getLogger("magi.mcp.worker")

#: How long the tool side waits for the worker to finish
#: processing a change job before giving up. Generous enough
#: for a stdio spawn + connect to settle; the worker's own
#: ``poll_seconds`` is much smaller, so this is mostly a safety
#: belt against a wedged DB / Worker.
_DEFAULT_TOOL_WAIT_TIMEOUT = 5.0


class McpWorker(RuntimeWorker):
    """Consumer that owns every MCP server connection in a MAGI process.

    Receives a fully-wired :class:`~magi.bus.Bus` via
    constructor injection. The :class:`changeMCPServerJobBoard` is
    drained in the background; :meth:`_bootstrap_connections`
    reads the current enabled set on startup. Every change job the
    worker claims carries enough payload to write
    :class:`~magi.bus.firmwares.books.local.mcpServerBook.McpServerBook`
    *and* refresh the live connection — the worker is the only
    code that touches either side after startup.
    """

    worker_name = "mcp"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds, concurrency=concurrency)
        self._connections: dict[str, MCPServerConnection] = {}
        self._server_locks: dict[str, asyncio.Lock] = {}

    # -- lifecycle --------------------------------------------------------

    async def on_start(self) -> None:
        # 1. Connect every currently-enabled row in parallel.
        await self._bootstrap_connections()

        # 2. Drain change jobs forever — every claim turns
        #    into a Book write + connection refresh.

    async def on_stopped(self) -> None:
        await self._disconnect_all()
        # Clear the source — the ToolsWorker listener will see
        # the empty list and republish the catalog without the
        # MCP tools on the next iteration.
        register_tools("mcp", [])

    # -- claim loop -------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            await self.reserve_capacity()
            try:
                job = await self.call(
                    self.bus.change_mcp_server_job_board.claim,
                    worker_id=self.worker_id,
                )
            except Exception:
                self.release_capacity()
                logger.exception("mcp worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                self.release_capacity()
                await asyncio.sleep(self.poll_seconds)
                continue
            self.spawn_reserved(
                self._handle_change_serialized(job),
                name=f"mcp-change-{job.job_id}",
            )

    async def _handle_change_serialized(self, job: ChangeMCPServerJob) -> None:
        """Handle different servers concurrently but serialize one server."""
        lock = self._server_locks.setdefault(job.server_name, asyncio.Lock())
        async with lock:
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
            servers = await self.call(self.bus.mcp_servers_book.list_enabled)
        except Exception:
            logger.exception("mcp worker: mcp_servers_book.list_enabled failed")
            register_tools("mcp", [])
            return

        if not servers:
            register_tools("mcp", [])
            logger.info("mcp worker: bootstrapped 0/0 servers (none enabled)")
            return

        timeouts = await self.call(self._timeouts_from_bus)
        connected: dict[str, MCPServerConnection] = {}

        async def _connect_one(server: Any) -> tuple[str, MCPServerConnection | None]:
            conn = self._build_connection(server)
            ok = await conn.connect(timeouts)
            return (server.name, conn if ok else None)

        # Explicit annotation sidesteps a Pylance inference quirk
        # in nested-closure coroutines: without it, ``results`` is
        # narrowed to ``BaseException`` and the ``for ... in``
        # below reports "object is not iterable".
        results: list[tuple[str, MCPServerConnection | None]] = await asyncio.gather(
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

    async def _handle_change(self, job: ChangeMCPServerJob) -> None:
        """Apply the change to the Book, then refresh the connection.

        Always :meth:`submit_result` so the Job Board reaches a
        terminal state — even unknown ``kind`` values report a
        failure back instead of leaking the row as ``processing``.
        The Book write happens before any connect / disconnect so
        a tool waiting on :meth:`wait_for_result` sees a fully
        consistent view: row on disk *and* matching connection in
        ``self._connections``.
        """
        name = job.server_name
        success = False
        error: str | None = None
        try:
            if job.kind == MCPKind.DELETED:
                await self.call(self.bus.mcp_servers_book.delete_by_name, name=name)
                await self._remove_server(name)
                success = True
            elif job.kind in (MCPKind.ADDED, MCPKind.UPDATED):
                if job.server is None:  # __post_init__ should have caught this
                    raise ValueError(f"kind={job.kind!r} requires server payload")
                await self.call(self._write_server, job.server)
                await self._reload_server_from_dto(job.server)
                success = True
            elif job.kind == MCPKind.TOGGLED:
                if job.new_enabled is None:  # __post_init__ should have caught this
                    raise ValueError("kind=MCPKind.TOGGLED requires new_enabled flag")
                await self.call(self._set_enabled, name=name, enabled=job.new_enabled)
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
            await self.call(
                self.bus.change_mcp_server_job_board.submit_result,
                job_id=job.job_id,
                worker_id=self.worker_id,
                result=ChangeMCPServerResult(
                    job_id=job.job_id,
                    status=JobStatus.COMPLETED if success else JobStatus.FAILED,
                    error=error,
                ),
            )
        except Exception:
            logger.exception("mcp worker: failed to submit result for %s", job.job_id)

    async def _remove_server(self, name: str) -> None:
        existing = self._connections.pop(name, None)
        if existing is not None:
            await existing.disconnect()
        self._reinject_tools()

    async def _reload_server(self, name: str) -> None:
        """Reload a single server by re-reading the row.

        Used after ``"toggled"`` (the Worker already wrote the
        new ``enabled`` flag, so a fresh read tells us whether to
        connect or just drop). ``added`` / ``updated`` go through
        :meth:`_reload_server_from_dto` instead — the Job
        payload already carries the row, no extra DB hop needed.
        """
        existing = self._connections.pop(name, None)
        if existing is not None:
            await existing.disconnect()

        try:
            row = await self.call(self.bus.mcp_servers_book.get_by_name, name=name)
        except Exception:
            logger.exception("mcp worker: mcp_servers_book.get_by_name failed for %r", name)
            row = None
        if row is None:
            self._reinject_tools()
            return
        if not row.enabled:
            self._reinject_tools()
            return

        timeouts = await self.call(self._timeouts_from_bus)
        conn = self._build_connection(row)
        if await conn.connect(timeouts):
            self._connections[name] = conn
        self._reinject_tools()

    async def _reload_server_from_dto(self, server: McpServer) -> None:
        """Reload a server using the DTO carried by the Job.

        Skips the DB read — the Worker just wrote this exact DTO
        to the Book, so going through :meth:`_reload_server`
        would be redundant.
        """
        existing = self._connections.pop(server.name, None)
        if existing is not None:
            await existing.disconnect()
        if not server.enabled:
            self._reinject_tools()
            return

        timeouts = await self.call(self._timeouts_from_bus)
        conn = self._build_connection(server)
        if await conn.connect(timeouts):
            self._connections[server.name] = conn
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

    # -- Book write helpers (sole writer) --------------------------------

    def _write_server(self, server: McpServer) -> None:
        """Upsert *server* into :class:`McpServerBook`.

        Translates the :class:`McpServer` DTO back into the
        Book's ``upsert`` kwargs (the Book's signature accepts
        primitives, not the DTO). Errors raised here propagate to
        :meth:`_handle_change`, which records them as the job
        result.
        """
        self.bus.mcp_servers_book.upsert(
            name=server.name,
            connection_type=server.connection_type,
            command=server.command,
            args=list(server.args),
            url=server.url,
            env=dict(server.env),
            headers=dict(server.headers),
            enabled=server.enabled,
            connect_timeout=server.connect_timeout,
            execute_timeout=server.execute_timeout,
            sse_read_timeout=server.sse_read_timeout,
        )

    def _set_enabled(self, *, name: str, enabled: bool) -> None:
        """Flip the ``enabled`` flag for *name*.

        Looks up the row first so we can pass ``server_id`` to
        :meth:`McpServerBook.update` (the Book's ``update``
        indexes by the autoincrement PK, not the operator-facing
        ``name``). ``None`` (row vanished mid-flight) is a no-op
        — the next claim will surface a fresh error if needed.
        """
        current = self.bus.mcp_servers_book.get_by_name(name=name)
        if current is None:
            return
        self.bus.mcp_servers_book.update(replace(current, enabled=enabled))

    # -- helpers ---------------------------------------------------------

    def _build_connection(
        self,
        server: Any,
    ) -> MCPServerConnection:
        """Wrap a DTO row in a fresh :class:`MCPServerConnection`.

        The row's own ``connect_timeout`` / ``execute_timeout`` /
        ``sse_read_timeout`` are passed through directly; at connect
        time the :class:`MCPServerConnection` falls back to the
        global defaults for any slot the row leaves blank.

        Imported lazily so the registry / startup code paths that
        import :class:`McpWorker` don't drag the ``mcp`` SDK at
        import time.
        """
        from magi.mcp.MCPClient import MCPServerConnection

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
        """Return the canonical MCP timeout defaults.

        MCP timeouts are an implementation detail of this subsystem;
        the values live on :class:`~magi.mcp.MCPClient.MCPTimeoutConfig`
        as field defaults rather than on the settings book.  This
        helper exists so call sites read symmetrically with the
        per-server row's optional overrides.
        """
        from magi.mcp.MCPClient import MCPTimeoutConfig

        return MCPTimeoutConfig()

    def _reinject_tools(self) -> None:
        """Aggregate tools from every live connection + republish.

        Fires the ``on_tools_changed`` listener registered by
        :class:`~magi.tools.worker.ToolsWorker` — the worker
        observes the dirty flag on its next iteration and
        republishes the catalog.
        """
        all_tools: list[Any] = [tool for conn in self._connections.values() for tool in conn.tools]
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


__all__ = ["McpWorker"]
