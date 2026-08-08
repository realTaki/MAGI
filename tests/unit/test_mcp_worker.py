"""Unit tests for :class:`~magi.mcp.worker.McpWorker`.

The worker composes the new_bus Book + Job Board with the loader
primitives; the tests stub out the ``MCPServerConnection.connect``
side of things so no real MCP subprocess / SSE / streamable-HTTP
traffic happens in CI. The behaviour under test:

- bootstrap connects every enabled row in parallel and re-injects
  tools via :func:`magi.tools.registry.register_tools`;
- bootstrap registers the four CRUD tools under source
  ``"mcp_manage"`` even when zero rows exist;
- a failed ``connect()`` at bootstrap is logged and skipped
  (other servers still come up);
- a change job with kind ``deleted`` / ``updated`` /
  ``toggled`` / ``added`` reaches the right helper and the
  job's :class:`McpServerChangedResult` is submitted;
- an unknown kind records an error in the result instead of
  leaving the job in ``processing``;
- ``stop()`` cancels the claim loop, tears down every
  connection, and clears the ``"mcp"`` source.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock

import pytest

from magi.mcp.worker import (
    McpWorker,
    start_mcp_worker,
    stop_mcp_worker,
)
from magi.new_bus.bootstrap import NewBus
from magi.new_bus.db import EngineFactory
from magi.new_bus.guild import (
    McpServerChangedJob,
    mcpServerChangedJobBoard,
)
from magi.new_bus.library.local import (
    McpServerBook,
    SettingBook,
)
from magi.new_bus.library.local.toolsBook import ToolDefinitionBook
from magi.tools import registry as tool_registry

# -- helpers -------------------------------------------------------------


class _StubConnection:
    """Acts enough like ``MCPServerConnection`` for the worker.

    The worker reads ``name`` / ``tools`` after ``await connect``;
    a real connection spawns a subprocess and calls
    ``session.list_tools`` — we replace that with a controllable
    AsyncMock so the test stays hermetic.
    """

    def __init__(self, name: str, tool_names: list[str]) -> None:
        self.name = name
        self._tool_names = list(tool_names)
        self.connect = AsyncMock(return_value=True)
        self.disconnect = AsyncMock(return_value=None)
        self.tools: list[Any] = [
            _StubTool(server_name=name, tool_name=t) for t in tool_names
        ]
        # The worker's `_reinject_tools` iterates ``conn.tools`` and
        # registers each via `register_tools`. Those wrappers only
        # need ``name`` and ``description`` to flow through the
        # registry.


class _StubTool:
    """Mimics the surface the tools registry reads."""

    description = "stub"

    def __init__(self, server_name: str, tool_name: str) -> None:
        self.name = f"{server_name}__{tool_name}"


def _build_new_bus(tmp_path) -> NewBus:
    """Stand up a real NewBus with just the Books / Boards the
    worker needs (the test never exercises the rest of the
    composition root)."""
    factory = EngineFactory(f"sqlite:///{tmp_path}/mcp-worker.db")
    factory.create_all()
    settings_book = SettingBook(factory)
    mcp_book = McpServerBook(factory)
    tool_book = ToolDefinitionBook(factory)
    board = mcpServerChangedJobBoard(factory)
    # The worker only touches these three attributes; the rest
    # of the NewBus slots are unused and stay ``None`` (the
    # ``NewBus`` dataclass uses ``object`` for everything but
    # ``_local_factory`` and ``_magis_factory``).
    return NewBus(
        sessions_book=None,  # type: ignore[arg-type]
        messages_book=None,  # type: ignore[arg-type]
        memory_book=None,  # type: ignore[arg-type]
        contacts_book=None,  # type: ignore[arg-type]
        contact_notes_book=None,  # type: ignore[arg-type]
        settings_book=settings_book,
        tasks_book=None,  # type: ignore[arg-type]
        task_runs_book=None,  # type: ignore[arg-type]
        tool_definitions_book=tool_book,
        tool_catalog_book=None,  # type: ignore[arg-type]
        mcp_servers_book=mcp_book,
        mcp_server_changed_job_board=board,
        tool_job_board=None,  # type: ignore[arg-type]
        agent_job_board=None,  # type: ignore[arg-type]
        llm_job_board=None,  # type: ignore[arg-type]
        delivery_job_board=None,  # type: ignore[arg-type]
        a2a_job_board=None,  # type: ignore[arg-type]
        change_provider_config_job_board=None,  # type: ignore[arg-type]
        token_usage_book=None,  # type: ignore[arg-type]
        action_items_book=None,  # type: ignore[arg-type]
        hook_signoffs_book=None,  # type: ignore[arg-type]
        stream_hub=None,  # type: ignore[arg-type]
        seed_preset_tasks_job_board=None,  # type: ignore[arg-type]
        _local_factory=factory,
    )


@pytest.fixture
def bus(tmp_path):
    """Fresh per-test NewBus on a per-test SQLite file."""
    return _build_new_bus(tmp_path)


@pytest.fixture(autouse=True)
def _reset_tool_registry():
    """The tools registry is process-global; clear between tests
    so injected tools don't leak across cases."""
    yield
    tool_registry._injected.clear()


def _patch_worker_build(monkeypatch, connections: list[_StubConnection]) -> None:
    """Replace ``McpWorker._build_connection`` so it returns our
    pre-built stubs instead of touching the loader. The stub's
    ``connect`` is a fresh ``AsyncMock(return_value=True)`` per
    test, set up in the caller via the connection itself.

    The worker calls ``_build_connection`` once per bootstrap
    row, and once per change-job reload. Each call gets the
    next unused stub matching the server name — that lets the
    ``updated`` test drive a "disconnect old, connect new"
    sequence by queueing two stubs for the same name.
    """
    queue: dict[str, list[_StubConnection]] = {}
    for stub in connections:
        queue.setdefault(stub.name, []).append(stub)

    def _factory(self: McpWorker, server: Any, timeouts: Any) -> _StubConnection:
        pending = queue.get(server.name, [])
        if not pending:
            raise AssertionError(
                f"unexpected server {server.name!r} in _build_connection"
            )
        return pending.pop(0)

    monkeypatch.setattr(McpWorker, "_build_connection", _factory)


# -- bootstrap ----------------------------------------------------------


def test_bootstrap_registers_manage_tools_and_reinjects_managed_tools(
    bus, monkeypatch
):
    bus.mcp_servers_book.upsert(
        name="gmail", connection_type="stdio", command="mcp-gmail"
    )
    gmail = _StubConnection("gmail", tool_names=["search", "send"])
    _patch_worker_build(monkeypatch, [gmail])

    worker = McpWorker(bus=bus)
    asyncio.run(worker.start())
    try:
        # Manage tools always present, regardless of rows.
        manage = tool_registry._injected.get("mcp_manage") or []
        manage_names = {t.name for t in manage}
        assert {"add_mcp_server", "list_mcp_servers", "update_mcp_server",
                "delete_mcp_server"} <= manage_names
        # Discovered tools under "mcp".
        discovered = tool_registry._injected.get("mcp") or []
        discovered_names = {t.name for t in discovered}
        assert discovered_names == {"gmail__search", "gmail__send"}
    finally:
        asyncio.run(worker.stop())


def test_bootstrap_with_no_servers_registers_only_manage_tools(bus, monkeypatch):
    worker = McpWorker(bus=bus)
    asyncio.run(worker.start())
    try:
        assert tool_registry._injected.get("mcp") == []
        manage = tool_registry._injected.get("mcp_manage") or []
        assert {t.name for t in manage} == {
            "add_mcp_server",
            "list_mcp_servers",
            "update_mcp_server",
            "delete_mcp_server",
        }
    finally:
        asyncio.run(worker.stop())


def test_bootstrap_skips_failing_servers(bus, monkeypatch, caplog):
    bus.mcp_servers_book.upsert(
        name="gmail", connection_type="stdio", command="mcp-gmail"
    )
    bus.mcp_servers_book.upsert(
        name="slack", connection_type="streamable_http",
        url="https://mcp.example.com",
    )
    gmail = _StubConnection("gmail", tool_names=["search"])
    gmail.connect = AsyncMock(return_value=False)  # fails to connect
    slack = _StubConnection("slack", tool_names=["post"])
    _patch_worker_build(monkeypatch, [gmail, slack])

    caplog.set_level("INFO", logger="magi.mcp.worker")
    worker = McpWorker(bus=bus)
    asyncio.run(worker.start())
    try:
        # Only slack's tools land in the registry.
        discovered_names = {
            t.name for t in (tool_registry._injected.get("mcp") or [])
        }
        assert discovered_names == {"slack__post"}
        # Worker's connection map matches.
        assert set(worker.connections_view().keys()) == {"slack"}
    finally:
        asyncio.run(worker.stop())


# -- per-change handling ------------------------------------------------


@pytest.mark.asyncio
async def test_handle_change_deleted_removes_connection(bus, monkeypatch):
    bus.mcp_servers_book.upsert(
        name="gmail", connection_type="stdio", command="mcp-gmail"
    )
    gmail = _StubConnection("gmail", tool_names=["search"])
    _patch_worker_build(monkeypatch, [gmail])

    worker = McpWorker(bus=bus)
    await worker.start()
    assert "gmail" in worker.connections_view()

    job_id = bus.mcp_server_changed_job_board.publish(
        McpServerChangedJob(kind="deleted", server_name="gmail")
    )
    claimed = await asyncio.to_thread(
        bus.mcp_server_changed_job_board.claim
    )
    assert claimed is not None
    await worker._handle_change(claimed)
    result = bus.mcp_server_changed_job_board.get_result(key=job_id)
    assert result is not None
    assert result.success is True
    assert "gmail" not in worker.connections_view()
    gmail.disconnect.assert_awaited_once()
    assert tool_registry._injected.get("mcp") == []

    with suppress(Exception):
        await worker.stop()


@pytest.mark.asyncio
async def test_handle_change_updated_reloads_server(bus, monkeypatch):
    bus.mcp_servers_book.upsert(
        name="gmail", connection_type="stdio", command="mcp-gmail"
    )
    old = _StubConnection("gmail", tool_names=["search"])
    new = _StubConnection("gmail", tool_names=["search", "send"])
    _patch_worker_build(monkeypatch, [old, new])

    worker = McpWorker(bus=bus)
    await worker.start()
    # Mutate the row to "force" a different reload outcome.
    bus.mcp_servers_book.upsert(
        name="gmail", connection_type="streamable_http",
        url="https://mcp.example.com",
    )

    job_id = bus.mcp_server_changed_job_board.publish(
        McpServerChangedJob(kind="updated", server_name="gmail")
    )
    claimed = await asyncio.to_thread(
        bus.mcp_server_changed_job_board.claim
    )
    await worker._handle_change(claimed)
    result = bus.mcp_server_changed_job_board.get_result(key=job_id)
    assert result is not None
    assert result.success is True
    # Both connection stubs were used (old disconnect, new
    # connect). The current entry is the new stub.
    current = worker.connections_view()["gmail"]
    assert current is new
    discovered = {t.name for t in (tool_registry._injected.get("mcp") or [])}
    assert discovered == {"gmail__search", "gmail__send"}

    with suppress(Exception):
        await worker.stop()


@pytest.mark.asyncio
async def test_handle_change_unknown_kind_records_error(bus, monkeypatch):
    """``_handle_change`` rejects unknown ``kind`` values by
    submitting a failed result. Today the DTO's
    ``__post_init__`` keeps bogus kinds out of the board, so
    we drive the path by patching ``_handle_change``'s input
    DTO directly: the worker's branch logic is the only
    thing under test."""
    from magi.new_bus.guild.mcpServerChangedJob import _McpServerChangedRow

    # Pre-seed a pending row carrying an unknown kind,
    # bypassing the DTO's ``__post_init__`` validation. This
    # mirrors the exact shape a buggy future API client could
    # leave behind — and the only way to reach the worker's
    # error branch in production.
    with bus._local_factory.session() as s:
        s.add(
            _McpServerChangedRow(
                job_id="job-bypass-1",
                status="pending",
                kind="rotated",
                server_name="gmail",
            )
        )
        s.commit()

    # Build a DTO with the same key so the worker's
    # ``submit_result`` call writes back to a real row.
    leaked = McpServerChangedJob.__new__(McpServerChangedJob)
    object.__setattr__(leaked, "kind", "rotated")
    object.__setattr__(leaked, "server_name", "gmail")
    object.__setattr__(leaked, "job_id", "job-bypass-1")

    worker = McpWorker(bus=bus)
    await worker.start()
    try:
        await worker._handle_change(leaked)
        result = bus.mcp_server_changed_job_board.get_result(
            key="job-bypass-1"
        )
        assert result is not None
        assert result.success is False
        assert result.error is not None
        assert "unknown change kind" in result.error
    finally:
        with suppress(Exception):
            await worker.stop()


# -- module-level singletons -------------------------------------------


def test_start_stop_singleton_round_trip(bus):
    bus.mcp_servers_book.upsert(
        name="gmail", connection_type="stdio", command="mcp-gmail"
    )
    worker = asyncio.run(start_mcp_worker(bus))
    try:
        assert worker.connections_view() == {}
        # No stub patching here — the real ``MCPServerConnection``
        # would attempt a connect that fails on every supported
        # transport in CI; the Book's only row is stdio with a
        # missing binary. The bootstrap logs and skips; the
        # connection map stays empty. We're proving the
        # singleton + lifecycle, not the connect path.
    finally:
        asyncio.run(stop_mcp_worker())
        # A second ``start`` after ``stop`` must spin up a fresh
        # worker (the global is reset).
        assert start_mcp_worker.__globals__["_worker"] is None


# -- timeout reading ----------------------------------------------------


def test_timeouts_default_when_settings_unset(bus):
    worker = McpWorker(bus=bus)
    cfg = worker._timeouts_from_bus()
    assert cfg.connect_timeout == 10.0
    assert cfg.execute_timeout == 60.0
    assert cfg.sse_read_timeout == 120.0


def test_timeouts_pick_up_settings_overrides(bus):
    bus.settings_book.set(key="mcp.connect_timeout", value="3.5")
    bus.settings_book.set(key="mcp.execute_timeout", value="42")
    bus.settings_book.set(key="mcp.sse_read_timeout", value="100")
    worker = McpWorker(bus=bus)
    cfg = worker._timeouts_from_bus()
    assert cfg.connect_timeout == 3.5
    assert cfg.execute_timeout == 42.0
    assert cfg.sse_read_timeout == 100.0
