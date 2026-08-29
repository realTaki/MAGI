"""Unit tests for :class:`~magi.mcp.worker.McpWorker`.

The worker is the sole writer to :class:`McpServerBook` and
owns every MCP server connection in one MAGI process. The
tests stub out the ``MCPServerConnection.connect`` side of
things so no real MCP subprocess / SSE / streamable-HTTP
traffic happens in CI. The behaviour under test:

- bootstrap connects every enabled row in parallel and
  re-injects the discovered tools via
  :func:`magi.tools.registry.register_tools`;
- a failed ``connect()`` at bootstrap is logged and skipped
  (other servers still come up);
- a change job with ``kind="added"`` / ``"updated"`` causes
  the Worker to write the Book (upsert) and reconnect;
- a change job with ``kind="deleted"`` causes the Worker to
  delete the Book row and tear down the connection;
- a change job with ``kind="toggled"`` causes the Worker to
  flip ``enabled`` and (re)connect / disconnect;
- the MCP CRUD tools (``add_mcp_server`` / ...) are registered
  via the builtin tools path, **not** by the worker;
- an unknown kind records an error in the result instead of
  leaking the job as ``processing``;
- ``stop()`` cancels the claim loop, tears down every
  connection, and clears the ``"mcp"`` source.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock

import pytest

from magi.old_bus.bootstrap import Bus
from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.bases.db.file import FileShelf
from magi.old_bus.firmwares.schema import LOCAL_SCOPE, synchronise_schema
from magi.old_bus.firmwares.jobs import (
    ChangeMCPServerJob,
    MCPKind,
    changeMCPServerJobBoard,
)
from magi.old_bus.bases.job import JobStatus
from magi.old_bus.firmwares.books.file.promptBook import PromptBook
from magi.old_bus.firmwares.books.local import (
    McpServerBook,
    SettingBook,
)
from magi.old_bus.firmwares.books.local.mcpServerBook import McpServer
from magi.old_bus.firmwares.books.local.toolsBook import ToolDefinitionBook
from magi.mcp.worker import McpWorker
from magi.tools import registry as tool_registry
from magi.tools.mcp.add_mcp_server import AddMcpServerTool
from magi.tools.mcp.delete_mcp_server import DeleteMcpServerTool
from magi.tools.mcp.list_mcp_servers import ListMcpServersTool
from magi.tools.mcp.update_mcp_server import UpdateMcpServerTool

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
        self.tools: list[Any] = [_StubTool(server_name=name, tool_name=t) for t in tool_names]


class _StubTool:
    """Mimics the surface the tools registry reads."""

    description = "stub"

    def __init__(self, server_name: str, tool_name: str) -> None:
        self.name = f"{server_name}__{tool_name}"


def _build_bus(tmp_path) -> Bus:
    """Stand up a real Bus with just the Books / Boards the
    worker needs (the test never exercises the rest of the
    composition root)."""
    factory = EngineFactory(f"sqlite:///{tmp_path}/mcp-worker.db")
    synchronise_schema(factory, scope=LOCAL_SCOPE)
    settings_book = SettingBook(factory)
    mcp_book = McpServerBook(factory)
    tool_book = ToolDefinitionBook(factory)
    board = changeMCPServerJobBoard(factory)
    # ``Bus.prompt_book`` is now non-Optional (B1: bootstrap fails
    # loudly on a missing prompts bundle), so the fixture must hand a
    # real PromptBook to the constructor even when the test never
    # touches prompt text. Empty tmp_path shelf — fine, the worker
    # under test doesn't read prompts.
    prompt_book = PromptBook(FileShelf(tmp_path / "prompts"))
    # The worker only touches these three attributes; the rest
    # of the Bus slots are unused and stay ``None`` (the
    # ``Bus`` dataclass uses ``object`` for everything but
    # ``_local_factory`` and ``_magis_factory``).
    return Bus(
        conversations_book=None,  # type: ignore[arg-type]
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
        change_mcp_server_job_board=board,
        prompt_book=prompt_book,
        tool_job_board=None,  # type: ignore[arg-type]
        agent_job_board=None,  # type: ignore[arg-type]
        llm_job_board=None,  # type: ignore[arg-type]
        delivery_notify_job_board=None,  # type: ignore[arg-type]
        a2a_request_job_board=None,  # type: ignore[arg-type]
        a2a_notify_job_board=None,  # type: ignore[arg-type]
        change_provider_config_job_board=None,  # type: ignore[arg-type]
        token_usage_book=None,  # type: ignore[arg-type]
        action_items_book=None,  # type: ignore[arg-type]
        hook_signoffs_book=None,  # type: ignore[arg-type]
        stream_hub=None,  # type: ignore[arg-type]
        seed_preset_task_job_board=None,  # type: ignore[arg-type]
        run_task_job_board=None,  # type: ignore[arg-type]
        _local_factory=factory,
    )


@pytest.fixture
def bus(tmp_path):
    """Fresh per-test Bus on a per-test SQLite file."""
    return _build_bus(tmp_path)


@pytest.fixture(autouse=True)
def _reset_tool_registry():
    """The tools registry is process-global; clear between tests
    so injected tools don't leak across cases. Both the
    injected source map and the builtin cache must drop —
    a previous test could have primed ``get_tool`` with a
    stale builtin list.
    """
    yield
    tool_registry._injected.clear()
    tool_registry._tools_cache = None


def _patch_worker_build(monkeypatch, connections: list[_StubConnection]) -> None:
    """Replace ``McpWorker._build_connection`` so it returns our
    pre-built stubs instead of touching the loader.

    The worker calls ``_build_connection`` once per bootstrap
    row, and once per change-job reload. Each call gets the
    next unused stub matching the server name — that lets the
    ``updated`` test drive a "disconnect old, connect new"
    sequence by queueing two stubs for the same name.
    """
    queue: dict[str, list[_StubConnection]] = {}
    for stub in connections:
        queue.setdefault(stub.name, []).append(stub)

    def _factory(self: McpWorker, server: Any) -> _StubConnection:
        pending = queue.get(server.name, [])
        if not pending:
            raise AssertionError(f"unexpected server {server.name!r} in _build_connection")
        return pending.pop(0)

    monkeypatch.setattr(McpWorker, "_build_connection", _factory)


def _dto(
    name: str,
    *,
    connection_type: str = "stdio",
    command: str | None = "mcp-stub",
    url: str | None = None,
    enabled: bool = True,
    args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> McpServer:
    """Build a minimal :class:`McpServer` DTO for change jobs."""
    return McpServer(
        name=name,
        connection_type=connection_type,
        command=command,
        args=args,
        url=url,
        env=dict(env or {}),
        headers=dict(headers or {}),
        enabled=enabled,
    )


# -- bootstrap ----------------------------------------------------------


def test_bootstrap_connects_enabled_rows_and_reinjects_tools(bus, monkeypatch):
    bus.mcp_servers_book.upsert(name="gmail", connection_type="stdio", command="mcp-gmail")
    gmail = _StubConnection("gmail", tool_names=["search", "send"])
    _patch_worker_build(monkeypatch, [gmail])

    worker = McpWorker(bus=bus)
    asyncio.run(worker.start())
    try:
        # Discovered tools under "mcp".
        discovered = tool_registry._injected.get("mcp") or []
        discovered_names = {t.name for t in discovered}
        assert discovered_names == {"gmail__search", "gmail__send"}
        # ``"mcp_manage"`` is NOT injected — the CRUD tools
        # are builtins, not under the worker's responsibility.
        assert "mcp_manage" not in tool_registry._injected
    finally:
        asyncio.run(worker.stop())


def test_bootstrap_with_no_servers_registers_empty_mcp(bus):
    worker = McpWorker(bus=bus)
    asyncio.run(worker.start())
    try:
        assert tool_registry._injected.get("mcp") == []
    finally:
        asyncio.run(worker.stop())


def test_bootstrap_skips_failing_servers(bus, monkeypatch, caplog):
    bus.mcp_servers_book.upsert(name="gmail", connection_type="stdio", command="mcp-gmail")
    bus.mcp_servers_book.upsert(
        name="slack",
        connection_type="streamable_http",
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
        discovered_names = {t.name for t in (tool_registry._injected.get("mcp") or [])}
        assert discovered_names == {"slack__post"}
        # Worker's connection map matches.
        assert set(worker.connections_view().keys()) == {"slack"}
    finally:
        asyncio.run(worker.stop())


def test_manage_tools_are_builtin_not_injected():
    """The CRUD tools live in :mod:`magi.tools.mcp` and are
    registered by the standard builtin tools path — the
    worker doesn't import or inject them. Verify they're
    reachable through ``get_tool`` without any injection.
    """
    # Force the builtin cache to rebuild with the four MCP tools.
    tool_registry._tools_cache = None
    builtins = {t.name for t in tool_registry.get_tool.__globals__["_build_tools"]()}
    assert {
        "add_mcp_server",
        "list_mcp_servers",
        "update_mcp_server",
        "delete_mcp_server",
    } <= builtins
    # And they're instantiable.
    assert AddMcpServerTool().name == "add_mcp_server"
    assert ListMcpServersTool().name == "list_mcp_servers"
    assert UpdateMcpServerTool().name == "update_mcp_server"
    assert DeleteMcpServerTool().name == "delete_mcp_server"


# -- per-change handling (worker is sole writer) ------------------------


@pytest.mark.asyncio
async def test_handle_change_added_writes_book_and_connects(bus, monkeypatch):
    """``kind="added"``: Worker upserts Book + connects."""
    gmail = _StubConnection("gmail", tool_names=["search"])
    _patch_worker_build(monkeypatch, [gmail])

    worker = McpWorker(bus=bus)
    # Bootstrap connections without spawning the claim loop: this test drives
    # the claimed job through ``_handle_change`` explicitly.
    await worker.on_start()
    # Bootstrap had no rows — Book is empty.
    assert bus.mcp_servers_book.get_by_name(name="gmail") is None

    job_id = bus.change_mcp_server_job_board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.ADDED,
            server_name="gmail",
            server=_dto("gmail", command="mcp-gmail"),
        )
    )
    claimed = await asyncio.to_thread(
        bus.change_mcp_server_job_board.claim, worker_id=worker.worker_id
    )
    assert claimed is not None
    await worker._handle_change(claimed)

    # Worker wrote the row.
    row = bus.mcp_servers_book.get_by_name(name="gmail")
    assert row is not None
    assert row.command == "mcp-gmail"
    # Worker connected (and disconnected old; here there was
    # no old, so just connected).
    assert "gmail" in worker.connections_view()

    result = bus.change_mcp_server_job_board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED

    with suppress(Exception):
        await worker.stop()


@pytest.mark.asyncio
async def test_handle_change_updated_reloads_server(bus, monkeypatch):
    """``kind="updated"``: Worker upserts Book + reconnects."""
    # Pre-seed so bootstrap consumes one stub. Without a
    # row at startup the worker's bootstrap skips the
    # connect call, and the queue's first entry would be
    # handed to the reload path — making the "old vs new"
    # assertion below meaningless.
    bus.mcp_servers_book.upsert(name="gmail", connection_type="stdio", command="mcp-gmail")
    old = _StubConnection("gmail", tool_names=["search"])
    new = _StubConnection("gmail", tool_names=["search", "send"])
    _patch_worker_build(monkeypatch, [old, new])

    worker = McpWorker(bus=bus)
    # Keep the durable claim under this test's control.
    await worker.on_start()
    assert worker.connections_view()["gmail"] is old

    job_id = bus.change_mcp_server_job_board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.UPDATED,
            server_name="gmail",
            server=_dto(
                "gmail",
                connection_type="streamable_http",
                command=None,
                url="https://mcp.example.com",
            ),
        )
    )
    claimed = await asyncio.to_thread(
        bus.change_mcp_server_job_board.claim, worker_id=worker.worker_id
    )
    await worker._handle_change(claimed)

    # Both connection stubs were used (old disconnect, new
    # connect). The current entry is the new stub.
    current = worker.connections_view()["gmail"]
    assert current is new
    # Book row reflects the new transport — the Worker is the
    # writer, so the row that the Job Board just submitted
    # is the only one we should see.
    row = bus.mcp_servers_book.get_by_name(name="gmail")
    assert row is not None
    assert row.connection_type == "streamable_http"
    assert row.url == "https://mcp.example.com"
    discovered = {t.name for t in (tool_registry._injected.get("mcp") or [])}
    assert discovered == {"gmail__search", "gmail__send"}

    result = bus.change_mcp_server_job_board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED

    with suppress(Exception):
        await worker.stop()


@pytest.mark.asyncio
async def test_handle_change_deleted_removes_book_row_and_connection(bus, monkeypatch):
    """``kind="deleted"``: Worker deletes Book + disconnects."""
    bus.mcp_servers_book.upsert(name="gmail", connection_type="stdio", command="mcp-gmail")
    gmail = _StubConnection("gmail", tool_names=["search"])
    _patch_worker_build(monkeypatch, [gmail])

    worker = McpWorker(bus=bus)
    # Keep the durable claim under this test's control.
    await worker.on_start()
    assert "gmail" in worker.connections_view()
    assert bus.mcp_servers_book.get_by_name(name="gmail") is not None

    job_id = bus.change_mcp_server_job_board.publish(
        ChangeMCPServerJob(kind=MCPKind.DELETED, server_name="gmail")
    )
    claimed = await asyncio.to_thread(
        bus.change_mcp_server_job_board.claim, worker_id=worker.worker_id
    )
    assert claimed is not None
    await worker._handle_change(claimed)

    assert "gmail" not in worker.connections_view()
    gmail.disconnect.assert_awaited_once()
    # Worker is the sole writer — it deletes the row.
    assert bus.mcp_servers_book.get_by_name(name="gmail") is None
    assert tool_registry._injected.get("mcp") == []

    result = bus.change_mcp_server_job_board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED

    with suppress(Exception):
        await worker.stop()


@pytest.mark.asyncio
async def test_handle_change_toggled_disables_and_disconnects(bus, monkeypatch):
    """``kind="toggled"``: Worker flips ``enabled`` to False,
    then the reload path drops the live connection (the row is
    still in the Book, but ``enabled=False`` so the connection
    shouldn't re-form)."""
    bus.mcp_servers_book.upsert(name="gmail", connection_type="stdio", command="mcp-gmail")
    gmail = _StubConnection("gmail", tool_names=["search"])
    _patch_worker_build(monkeypatch, [gmail])

    worker = McpWorker(bus=bus)
    # Keep the durable claim under this test's control.
    await worker.on_start()
    assert "gmail" in worker.connections_view()

    job_id = bus.change_mcp_server_job_board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.TOGGLED,
            server_name="gmail",
            new_enabled=False,
        )
    )
    claimed = await asyncio.to_thread(
        bus.change_mcp_server_job_board.claim, worker_id=worker.worker_id
    )
    await worker._handle_change(claimed)

    # Worker updated the Book and dropped the connection.
    row = bus.mcp_servers_book.get_by_name(name="gmail")
    assert row is not None
    assert row.enabled is False
    assert "gmail" not in worker.connections_view()

    result = bus.change_mcp_server_job_board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED

    with suppress(Exception):
        await worker.stop()


@pytest.mark.asyncio
async def test_handle_change_toggled_enables_and_connects(bus, monkeypatch):
    """``kind="toggled"`` with ``new_enabled=True``: Worker
    flips the flag and (re)connects."""
    bus.mcp_servers_book.upsert(
        name="gmail",
        connection_type="stdio",
        command="mcp-gmail",
        enabled=False,
    )
    # No bootstrap connection — enabled=False at startup.
    new = _StubConnection("gmail", tool_names=["search"])
    _patch_worker_build(monkeypatch, [new])

    worker = McpWorker(bus=bus)
    # Keep the durable claim under this test's control.
    await worker.on_start()
    assert "gmail" not in worker.connections_view()

    job_id = bus.change_mcp_server_job_board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.TOGGLED,
            server_name="gmail",
            new_enabled=True,
        )
    )
    claimed = await asyncio.to_thread(
        bus.change_mcp_server_job_board.claim, worker_id=worker.worker_id
    )
    await worker._handle_change(claimed)

    row = bus.mcp_servers_book.get_by_name(name="gmail")
    assert row is not None
    assert row.enabled is True
    assert "gmail" in worker.connections_view()

    result = bus.change_mcp_server_job_board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED

    with suppress(Exception):
        await worker.stop()


# NOTE: a prior ``test_handle_change_unknown_kind_records_error`` lived
# here. With the ``kind`` column promoted to ``SAEnum(MCPKind, ...)``
# the DB CHECK constraint rejects unknown kinds at INSERT time, so
# the worker's defensive ``else: error = "unknown change kind"``
# branch can no longer be reached from a published job. The
# enforcement now lives in :class:`magi.bus.firmwares.jobs.changeMCPServerJob._ChangeMCPServerRow`'s
# column type, not the worker — see the StrEnum/SAEnum migration
# docs.

# -- module-level singletons -------------------------------------------


def test_start_stop_lifecycle_round_trip(bus):
    bus.mcp_servers_book.upsert(name="gmail", connection_type="stdio", command="mcp-gmail")
    worker = McpWorker(bus)
    asyncio.run(worker.start())
    try:
        assert worker.connections_view() == {}
        # No stub patching here — the real ``MCPServerConnection``
        # would attempt a connect that fails on every supported
        # transport in CI; the Book's only row is stdio with a
        # missing binary. The bootstrap logs and skips; the
        # connection map stays empty. We're proving the
        # singleton + lifecycle, not the connect path.
    finally:
        asyncio.run(worker.stop())


# -- timeout reading ----------------------------------------------------


def test_timeouts_default_when_settings_unset(bus):
    worker = McpWorker(bus=bus)
    cfg = worker._timeouts_from_bus()
    assert cfg.connect_timeout == 10.0
    assert cfg.execute_timeout == 60.0
    assert cfg.sse_read_timeout == 120.0
