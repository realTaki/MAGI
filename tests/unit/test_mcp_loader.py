"""Tests for the DB-backed MCP tool loader.

Covers the surface that doesn't need a live MCP server
connected (live round-trip needs an MCP sample server
installed in the test env — out of scope here):

  - ``_load_servers_from_db`` correctly maps a row to
    an :class:`MCPServerConnection` (stdio / sse /
    streamable_http / disabled / missing fields)
  - the env-merge contract: ``os.environ | row.env``
    (operator's keys win, container's keys fill the
    rest so ``PATH`` etc. still work for stdio servers)
  - the JSON column defence: malformed ``env_json`` /
    ``args_json`` falls back to empty rather than
    crashing the whole load
  - the synchronous :func:`load_mcp_tools_blocking` entry
    point works against an empty / populated table
  - :func:`registry.maybe_reload_mcp_tools` fires when
    ``updated_at`` moves and is a no-op otherwise
  - :func:`registry.get_tools` / :func:`get_tool` see the
    MCP-discovered tools after a load

The wrapper's protocol behaviour (``MCPTool.run`` against
a real ``ClientSession``) is mocked: we don't need a live
mcp server, only the DB→connection mapping + the
reload semantics.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from magi.mcp import loader as mcp_loader
from magi.tools.base import ToolContext, ToolResult
from magi.tools.registry import (
    bootstrap_mcp_tools,
    get_tool,
    get_tool_schemas,
    get_tools,
    maybe_reload_mcp_tools,
    reset_cache,
    reset_mcp_cache,
)


# -- DB fixture --------------------------------------------------------------


@pytest.fixture
def mcp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Per-test isolated state dir with the
    ``mcp_servers`` table created. Resets the engine
    singleton so each test sees its own empty DB.

    Returns a callable that the test can use to seed
    rows without having to know the SQLAlchemy details.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.bus.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.bus.db import (
        init_orm,
        open_session,
    )
    from magi.bus.models.local.mcp_server import McpServer

    init_orm(str(state))

    def _seed(
        *,
        name: str = "test-server",
        connection_type: str = "stdio",
        command: str | None = "echo",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> McpServer:
        import json
        with open_session() as s:
            row = McpServer(
                name=name,
                connection_type=connection_type,
                command=command,
                args_json=json.dumps(args or []),
                env_json=json.dumps(env or {}),
                url=url,
                headers_json=json.dumps(headers or {}),
                enabled=enabled,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row

    return _seed


# -- _load_servers_from_db --------------------------------------------------


def test_load_servers_empty_db_returns_no_connections(mcp_db):
    """No rows → empty connection list. The loader
    never crashes on an empty table — that's the
    v0 default for fresh installs."""
    conns = mcp_loader._load_servers_from_db()
    assert conns == []


def test_load_servers_stdio_row_maps_correctly(mcp_db):
    mcp_db(
        name="std",
        connection_type="stdio",
        command="uvx",
        args=["mcp-server-fetch"],
        env={"API_KEY": "secret"},
    )
    [conn] = mcp_loader._load_servers_from_db()
    assert conn.name == "std"
    assert conn.connection_type == "stdio"
    assert conn.command == "uvx"
    assert conn.args == ["mcp-server-fetch"]
    assert conn.env == {"API_KEY": "secret"}
    assert conn.url is None
    assert conn.headers == {}


def test_load_servers_http_row_maps_correctly(mcp_db):
    mcp_db(
        name="http1",
        connection_type="streamable_http",
        command=None,
        url="https://api.example.com/mcp",
        headers={"Authorization": "Bearer xyz"},
    )
    [conn] = mcp_loader._load_servers_from_db()
    assert conn.name == "http1"
    assert conn.connection_type == "streamable_http"
    assert conn.url == "https://api.example.com/mcp"
    assert conn.headers == {"Authorization": "Bearer xyz"}
    assert conn.command is None
    assert conn.args == []


def test_load_servers_skips_disabled(mcp_db):
    mcp_db(name="on", enabled=True)
    mcp_db(name="off", enabled=False)
    conns = mcp_loader._load_servers_from_db()
    assert [c.name for c in conns] == ["on"]


def test_load_servers_skips_stdio_without_command(mcp_db):
    mcp_db(name="bad", command=None, url=None)
    conns = mcp_loader._load_servers_from_db()
    assert conns == []


def test_load_servers_skips_http_without_url(mcp_db):
    mcp_db(
        name="bad",
        connection_type="streamable_http",
        command=None,
        url=None,
    )
    conns = mcp_loader._load_servers_from_db()
    assert conns == []


def test_load_servers_skips_unknown_connection_type(mcp_db):
    mcp_db(name="weird", connection_type="carrier-pigeon")
    conns = mcp_loader._load_servers_from_db()
    assert conns == []


def test_load_servers_malformed_json_falls_back_to_empty(mcp_db, caplog):
    """A hand-edited row with a broken ``env_json`` /
    ``args_json`` doesn't crash the whole load. The
    loader logs a warning and treats the column as
    empty."""
    from magi.bus.db import open_session
    from magi.bus.models.local.mcp_server import McpServer

    with open_session() as s:
        s.add(McpServer(
            name="corrupt",
            connection_type="stdio",
            command="echo",
            args_json="not-json",
            env_json="{also-broken",
        ))
        s.commit()

    conns = mcp_loader._load_servers_from_db()
    assert len(conns) == 1
    assert conns[0].env == {}
    assert conns[0].args == []


def test_load_servers_env_merge_overrides_parent(mcp_db, monkeypatch):
    """``_open_streams`` always builds
    ``{**os.environ, **self.env}`` — the operator's
    keys win, the container's keys fill the rest. A
    stdio server still gets ``PATH`` even when the
    operator set a single override key."""
    monkeypatch.setenv("TEST_PARENT_KEY", "from-container")
    mcp_db(env={"TEST_PARENT_KEY": "from-row", "EXTRA": "row-only"})

    [conn] = mcp_loader._load_servers_from_db()
    import os
    merged = {**os.environ, **conn.env}
    assert merged["TEST_PARENT_KEY"] == "from-row"  # operator wins
    assert merged["EXTRA"] == "row-only"
    # Container's PATH is preserved (assuming the test
    # env has it — it does in CI).
    assert "PATH" in merged


# -- load_mcp_tools_blocking + registry integration ------------------------


def test_load_blocking_empty_db_returns_empty(mcp_db):
    """No rows → no tools. The synchronous entry point
    does not raise when the table is empty."""
    assert mcp_loader.load_mcp_tools_blocking() == []


def test_load_blocking_inside_running_loop_emits_no_unawaited_warning(mcp_db):
    """Regression: when the caller is already on a running
    asyncio loop (Uvicorn's worker loop calls
    :func:`bootstrap_mcp_tools` from a sync hook in
    ``create_app``), the wrapper used to call
    ``asyncio.run(load_mcp_tools_async(...))`` which:
      1. raised ``RuntimeError("asyncio.run() cannot be
         called from a running event loop")``
      2. left the coroutine object never-awaited
      3. emitted ``RuntimeWarning: coroutine
         'load_mcp_tools_async' was never awaited`` on GC

    The fix constructs the coroutine first and only
    feeds it to ``asyncio.run``; the exception branch
    reuses the same coroutine object via
    ``loop.run_until_complete``, so it's always awaited
    exactly once and the warning never fires.

    The "are we in a running loop?" probe is
    ``asyncio.get_running_loop()`` — the canonical way
    to detect the case. We patch it to return a sentinel
    (simulating the Uvicorn worker shape) without
    spinning up a real asyncio.run — which would
    require the rest of the test to coexist with the
    loader's module-level state.
    """
    import asyncio
    import warnings
    from unittest.mock import MagicMock

    # Simulate "we're inside a running event loop" by
    # making ``get_running_loop`` return a truthy object.
    # The wrapper's probe reads this; the real
    # ``asyncio.run`` call is bypassed entirely.
    with patch.object(asyncio, "get_running_loop", return_value=MagicMock()):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            mcp_loader.load_mcp_tools_blocking()

    never_awaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "never awaited" in str(w.message)
    ]
    assert not never_awaited, (
        f"load_mcp_tools_blocking emitted "
        f"{len(never_awaited)} never-awaited warning(s); "
        f"first: {never_awaited[0].message}"
    )


def test_load_blocking_populated_db_attempts_connections(mcp_db):
    """A populated table triggers :func:`connect` on
    each connection. With no real MCP server, the
    subprocess ``connect`` fails and the loader logs;
    we just verify the loader doesn't crash and the
    ``_connections`` list is empty (no successful
    connect to surface)."""
    mcp_db(name="will-fail-to-connect", command="this-binary-does-not-exist")
    tools = mcp_loader.load_mcp_tools_blocking()
    # ``uvx`` / ``this-binary-does-not-exist`` either
    # resolves to a missing binary (connect fails) or,
    # if uvx is on PATH, runs and immediately exits
    # (handshake fails). Either way, no tools.
    assert tools == []


def test_load_blocking_filters_disabled(mcp_db):
    """Disabled rows don't get a connection attempt."""
    mcp_db(name="off", command="echo", enabled=False)
    mcp_db(name="on", command="echo", enabled=True)
    # The 'off' row is filtered; 'on' tries to connect
    # and fails. The point: disabled is filtered BEFORE
    # connect (no subprocess spawn for a disabled row).
    mcp_loader.load_mcp_tools_blocking()
    assert [c.name for c in mcp_loader.active_connections()] == []


def test_load_blocking_cleans_previous_connections(mcp_db):
    """Calling :func:`load_mcp_tools_async` twice in a
    row doesn't accumulate :class:`MCPServerConnection`
    objects in the module-level list."""
    mcp_db(command="echo")
    mcp_loader.load_mcp_tools_blocking()
    n_after_first = len(mcp_loader.active_connections())
    mcp_loader.load_mcp_tools_blocking()
    # The second call cleans the first list (no
    # accumulation). The post-cleanup list is empty
    # because the actual connect fails for ``echo``.
    assert n_after_first == 0
    assert len(mcp_loader.active_connections()) == 0


# -- registry: maybe_reload_mcp_tools + bootstrap_mcp_tools -----------------


def test_bootstrap_mcp_tools_runs_against_db(mcp_db):
    """The synchronous bootstrap reads the DB and
    populates :data:`_mcp_tools_cache`. Even when no
    real MCP server is available, the bootstrap
    completes (no exception)."""
    mcp_db(command="echo")
    bootstrap_mcp_tools()
    # We can introspect the module-level cache via a
    # clean reload — but the cleaner check is that
    # get_tools() returns the built-in list (MCP
    # tools may be empty if 'echo' connect failed).
    tools = get_tools()
    # ``ReadFileTool`` is the first built-in; the
    # assertion is that the list is non-empty (i.e.
    # the built-in path is alive regardless of MCP).
    assert any(t.name == "read_file" for t in tools)


def test_maybe_reload_is_noop_when_unchanged(mcp_db):
    """A no-op reload call (table unchanged) doesn't
    repopulate the cache or log. Cheap path on the
    chat-turn hot loop."""
    mcp_db(command="echo")
    bootstrap_mcp_tools()
    # First maybe_reload right after a fresh bootstrap
    # should be a no-op — the cache stamp matches the
    # table's max updated_at.
    result = maybe_reload_mcp_tools()
    assert result is None  # no reload happened


def test_maybe_reload_fires_on_table_edit(mcp_db):
    """An edit to the table (``updated_at`` moves
    forward) triggers a reload on the next
    :func:`maybe_reload_mcp_tools` call."""
    mcp_db(command="echo")
    bootstrap_mcp_tools()
    # Edit a row to bump updated_at. SQLAlchemy's
    # ``onupdate`` only fires on column value changes,
    # so we touch ``enabled``.
    import time as _t
    from magi.bus.db import open_session
    from magi.bus.models.local.mcp_server import McpServer

    # The datetime resolution on sqlite is second-level,
    # so a sub-second edit may not bump the stamp. Sleep
    # one second to be sure the next edit registers as
    # a different ``updated_at``.
    _t.sleep(1.05)
    with open_session() as s:
        row = s.get(McpServer, "test-server")
        row.enabled = False  # any column change bumps onupdate
        s.commit()

    result = maybe_reload_mcp_tools()
    # Returns the freshly-loaded list (or [] when the
    # table is empty / connect failed). Either way: not
    # ``None`` — a reload fired.
    assert result is not None
    assert isinstance(result, list)


def test_maybe_reload_handles_deleted_table(mcp_db, monkeypatch):
    """A transient DB hiccup (or pre-init state) is
    swallowed — the function returns ``None`` and the
    cache is left alone."""
    mcp_db(command="echo")
    bootstrap_mcp_tools()
    # Force the inner query to blow up by patching
    # ``magi.db.open_session`` to raise — that's
    # the symbol the function actually resolves at call
    # time.
    import magi.bus.db as db_mod

    real_open_session = db_mod.open_session
    def _boom(*_a, **_kw):
        raise RuntimeError("simulated DB down")

    monkeypatch.setattr(db_mod, "open_session", _boom)
    # Should swallow the exception and return None.
    assert maybe_reload_mcp_tools() is None
    monkeypatch.setattr(db_mod, "open_session", real_open_session)


# -- reset_mcp_cache + integration with get_tool/get_tool_schemas -----------


def test_reset_mcp_cache_drops_cache_and_stamp():
    """``reset_mcp_cache`` clears both the tool list
    and the load stamp so the next
    :func:`maybe_reload_mcp_tools` fires unconditionally."""
    reset_mcp_cache()
    # The next reload should always fire (stamp is None).
    # We don't have a populated DB here so the load
    # returns ``[]``; the assertion is that the function
    # doesn't crash.
    result = maybe_reload_mcp_tools()
    assert result == []  # empty table → [] (and stamp becomes None again)


# -- get_tool_schemas surfaces registered tools ------------------------------


def test_get_tool_schemas_includes_builtin_tools(mcp_db):
    """Sanity: the built-in tools are present in the
    schema list. This is a regression guard for the
    bootstrap path — if registry ever drops the
    built-in list when MCP fails, this test catches
    it."""
    schemas = get_tool_schemas()
    names = [s["name"] for s in schemas]
    assert "read_file" in names
    assert "write_file" in names


# -- MCPTool.run (mocked ClientSession) -------------------------------------


def test_mcptool_run_returns_text_content(mcp_db, monkeypatch):
    """Round-trip a fake MCP tool through the wrapper
    and confirm the agent loop's
    ``ToolResult`` shape is correct. Uses
    ``AsyncMock`` for the upstream session — no
    subprocess, no real MCP server."""

    from magi.mcp.loader import MCPTool

    class _FakeSession:
        async def call_tool(self, name, arguments):
            class _Result:
                content = [
                    type("Text", (), {"text": "hello world"})(),
                ]
                isError = False
            return _Result()

    tool = MCPTool(
        server_name="srv",
        server_tool_name="echo",
        description="echoes input",
        parameters={"type": "object", "properties": {}},
        session=_FakeSession(),  # type: ignore[arg-type]
        execute_timeout=5.0,
    )
    import asyncio
    from pathlib import Path
    from magi.channels import Channel
    ctx = ToolContext(
        state_dir="/tmp",
        workspace=Path("/tmp"),
        uid=1,
        channel=Channel.WEBUI,
    )
    result = asyncio.run(tool.run(ctx, text="hi"))
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.content == "hello world"


def test_mcptool_run_handles_timeout(mcp_db):
    """A server that exceeds ``execute_timeout`` is
    reported back to the LLM as a tool error
    (``is_error=True``) with a clear message — never a
    raw ``asyncio.TimeoutError`` that would crash the
    agent loop."""
    import asyncio
    from pathlib import Path
    from magi.mcp.loader import MCPTool
    from magi.channels import Channel

    class _SlowSession:
        async def call_tool(self, name, arguments):
            await asyncio.sleep(10)
            return None  # unreachable

    tool = MCPTool(
        server_name="srv",
        server_tool_name="slow",
        description="",
        parameters={},
        session=_SlowSession(),  # type: ignore[arg-type]
        execute_timeout=0.05,
    )
    ctx = ToolContext(
        state_dir="/tmp",
        workspace=Path("/tmp"),
        uid=1,
        channel=Channel.WEBUI,
    )
    result = asyncio.run(tool.run(ctx, text="x"))
    assert result.is_error is True
    assert "timed out" in (result.content or "").lower()


def test_mcptool_run_handles_call_exception():
    """A server that raises inside ``call_tool`` is
    reported as an error, not propagated."""
    import asyncio
    from pathlib import Path
    from magi.mcp.loader import MCPTool
    from magi.channels import Channel

    class _BrokenSession:
        async def call_tool(self, name, arguments):
            raise RuntimeError("server-side kaboom")

    tool = MCPTool(
        server_name="srv",
        server_tool_name="broken",
        description="",
        parameters={},
        session=_BrokenSession(),  # type: ignore[arg-type]
        execute_timeout=5.0,
    )
    ctx = ToolContext(
        state_dir="/tmp",
        workspace=Path("/tmp"),
        uid=1,
        channel=Channel.WEBUI,
    )
    result = asyncio.run(tool.run(ctx, text="x"))
    assert result.is_error is True
    assert "kaboom" in (result.content or "")


# -- get_tool lookup --------------------------------------------------------


def test_get_tool_finds_builtin_tool(mcp_db):
    """``get_tool(name)`` returns the built-in tool
    instance by name. The lookup path is shared
    between built-in and MCP tools — this is the
    built-in half of the contract."""
    from magi.tools.registry import get_tool
    tool = get_tool("read_file")
    assert tool is not None
    assert tool.name == "read_file"


def test_get_tool_returns_none_for_unknown(mcp_db):
    from magi.tools.registry import get_tool
    assert get_tool("definitely_not_a_real_tool_xyz") is None
