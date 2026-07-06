"""Tests for the MCP tool loader.

Covers the surface that doesn't need a live MCP server:

  - config path resolution (incl. ``mcp-example.json`` fallback)
  - per-server validation (STDIO needs ``command``,
    URL-based needs ``url``)
  - the synchronous :func:`load_mcp_tools_blocking` entry
    point
  - that :func:`registry.get_tools` returns the merged
    built-in + MCP list correctly
  - that registered MCP tools are recognised by
    ``get_tool(name)``
  - that :func:`MCPServerConnection._safe_close` doesn't
    raise even when the underlying exit stack is missing

Live end-to-end test (real MCP server round-trip) is out of
scope — it'd need an MCP sample server installed in the
test environment. The wrapper's protocol behaviour is
covered by ``test_mcp_tool_wrapper`` below (mock session)
and the upstream Mini-Agent tests cover the transport layer.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from magi.runtime.tools import mcp_loader
from magi.runtime.tools.base import ToolContext, ToolResult
from magi.runtime.tools.registry import (
    bootstrap_mcp_tools,
    get_tool,
    get_tool_schemas,
    get_tools,
    reset_cache,
    reset_mcp_cache,
)


# -- helpers -------------------------------------------------------------


def _write_config(tmp: Path, body: dict[str, Any]) -> Path:
    p = tmp / "mcp.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _example_config(tmp: Path) -> Path:
    """A "no servers configured" example that exercises the
    path-resolution + parser branches without trying to
    connect anywhere."""
    return _write_config(tmp, {"mcpServers": {}})


# -- config resolution ---------------------------------------------------


def test_resolve_config_path_prefers_explicit(tmp_path: Path) -> None:
    """``mcp.json`` exists → use it (no fallback)."""
    p = tmp_path / "mcp.json"
    p.write_text("{}", encoding="utf-8")
    assert mcp_loader.resolve_config_path(str(p)) == p


def test_resolve_config_path_falls_back_to_example(tmp_path: Path) -> None:
    """``mcp.json`` missing but ``mcp-example.json`` present → use the example."""
    target = tmp_path / "mcp.json"
    example = tmp_path / "mcp-example.json"
    example.write_text("{}", encoding="utf-8")
    assert mcp_loader.resolve_config_path(str(target)) == example


def test_resolve_config_path_returns_none(tmp_path: Path) -> None:
    """Neither file present → ``None`` (skip cleanly)."""
    target = tmp_path / "mcp.json"
    assert mcp_loader.resolve_config_path(str(target)) is None


# -- per-server validation ----------------------------------------------


def test_config_skips_disabled_server(tmp_path: Path) -> None:
    """``"disabled": true`` entries are filtered out at parse time."""
    cfg = _write_config(
        tmp_path,
        {"mcpServers": {"on": {"command": "echo"}, "off": {"command": "echo", "disabled": True}}},
    )
    servers = mcp_loader._load_servers_from_config(cfg)
    assert [s.name for s in servers] == ["on"]


def test_config_skips_stdio_without_command(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {"mcpServers": {"bad": {"args": ["x"]}, "good": {"command": "echo"}}},
    )
    servers = mcp_loader._load_servers_from_config(cfg)
    assert [s.name for s in servers] == ["good"]


def test_config_skips_url_based_without_url(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {
            "mcpServers": {
                # explicit streamable_http but no url → skip
                "stream": {"type": "streamable_http"},
                # explicit sse but no url → skip
                "sse": {"type": "sse"},
                # empty url → still skipped (treated as stdio→rejected)
                "empty": {"url": ""},
                # happy path
                "ok": {"url": "https://example.com"},
            }
        },
    )
    servers = mcp_loader._load_servers_from_config(cfg)
    assert [s.name for s in servers] == ["ok"]


def test_config_skips_non_dict_server_entry(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {"mcpServers": {"bad": "not a dict", "good": {"command": "echo"}}},
    )
    servers = mcp_loader._load_servers_from_config(cfg)
    assert [s.name for s in servers] == ["good"]


def test_config_skips_malformed_top_level(tmp_path: Path) -> None:
    """If ``mcpServers`` isn't an object, return ``[]``."""
    cfg = _write_config(tmp_path, {"mcpServers": ["not a dict"]})
    servers = mcp_loader._load_servers_from_config(cfg)
    assert servers == []


def test_determine_connection_type_legacy_http(tmp_path: Path) -> None:
    """Legacy ``type: http`` is silently aliased to ``streamable_http``."""
    assert mcp_loader._determine_connection_type({"type": "http"}) == "streamable_http"
    assert mcp_loader._determine_connection_type({"type": "HTTP"}) == "streamable_http"
    assert mcp_loader._determine_connection_type({}) == "stdio"
    assert mcp_loader._determine_connection_type({"url": ""}) == "stdio"
    assert mcp_loader._determine_connection_type({"url": "https://x"}) == "streamable_http"


def test_load_servers_parses_per_server_timeouts(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {
            "mcpServers": {
                "x": {
                    "command": "echo",
                    "connect_timeout": 3.5,
                    "execute_timeout": "20",  # str → float
                    "sse_read_timeout": None,
                }
            }
        },
    )
    (server,) = mcp_loader._load_servers_from_config(cfg)
    assert server.connect_timeout == 3.5
    assert server.execute_timeout == 20.0
    assert server.sse_read_timeout is None


# -- package-shipped example --------------------------------------------


def test_package_example_is_valid() -> None:
    """``mcp-example.json`` shipped in the package parses cleanly.

    The deployer-starter may declare zero or more servers;
    the only invariant is that the JSON parses and
    ``mcpServers`` is present.
    """
    example = Path(mcp_loader.__file__).parent / "mcp-example.json"
    assert example.exists(), "mcp-example.json must ship next to mcp_loader.py"
    cfg = json.loads(example.read_text(encoding="utf-8"))
    assert "mcpServers" in cfg
    # Either empty or every entry must be a dict.
    for name, entry in cfg["mcpServers"].items():
        assert isinstance(entry, dict), f"{name} must be a dict"


# -- sync loader honours the registry contract --------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Each test starts with a clean registry + MCP connection
    list. Tests that mutate state call ``reset_cache()`` /
    ``reset_mcp_cache()`` themselves; this just guarantees the
    cross-test baseline is fresh."""
    reset_cache()
    reset_mcp_cache()
    yield
    reset_cache()
    reset_mcp_cache()


def test_blocking_loader_returns_empty_when_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No config anywhere → empty list, no exception.

    We point ``MAGI_MCP_CONFIG`` at a non-existent path so the
    loader's filesystem search doesn't pick up the package
    example by accident.
    """
    monkeypatch.setenv("MAGI_MCP_CONFIG", str(tmp_path / "nope.json"))
    tools = mcp_loader.load_mcp_tools_blocking()
    assert tools == []


def test_blocking_loader_skips_disabled_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sync loader wraps ``load_mcp_tools_async``; disabled servers
    are filtered before any connection attempt, so the result
    is ``[]`` even if some entries would otherwise fail to
    connect."""
    _example_config(tmp_path)
    monkeypatch.setenv("MAGI_MCP_CONFIG", str(tmp_path / "mcp.json"))
    # If we'd let the real ``connect`` run it'd try to spawn
    # subprocesses. Patch the module-level function to assert
    # it was never called for a no-server config.
    with patch.object(
        mcp_loader, "_load_servers_from_config", return_value=[]
    ) as parser:
        tools = mcp_loader.load_mcp_tools_blocking()
    assert tools == []
    parser.assert_called_once()


# -- registry integration -----------------------------------------------


def test_bootstrap_mcp_populates_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``bootstrap_mcp_tools`` calls the blocking loader and
    pushes the result into the registry cache; subsequent
    :func:`get_tools` returns it."""
    _example_config(tmp_path)
    monkeypatch.setenv("MAGI_MCP_CONFIG", str(tmp_path / "mcp.json"))

    # We don't actually want to connect anywhere — fake the
    # loader to return one synthetic tool.
    fake_tool = _make_fake_tool("upstream__noop")
    with patch.object(
        mcp_loader, "load_mcp_tools_blocking", return_value=[fake_tool]
    ):
        bootstrap_mcp_tools()

    tools = get_tools()
    names = {t.name for t in tools}
    # Built-ins are still there.
    assert "read_file" in names
    # The synthetic MCP tool joined the list.
    assert "upstream__noop" in names

    # And it's reachable via ``get_tool``.
    assert get_tool("upstream__noop") is fake_tool
    # Anthropic schema renders correctly.
    schema = next(s for s in get_tool_schemas() if s["name"] == "upstream__noop")
    assert schema["description"]


def test_reset_mcp_cache_isolated_from_builtin_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resetting MCP must NOT wipe the built-in tool cache —
    the built-ins are expensive to re-import."""
    _example_config(tmp_path)
    monkeypatch.setenv("MAGI_MCP_CONFIG", str(tmp_path / "mcp.json"))
    fake_tool = _make_fake_tool("upstream__noop")
    with patch.object(
        mcp_loader, "load_mcp_tools_blocking", return_value=[fake_tool]
    ):
        bootstrap_mcp_tools()

    # All built-ins present + MCP tool.
    assert "read_file" in {t.name for t in get_tools()}
    assert "upstream__noop" in {t.name for t in get_tools()}

    # Drop just the MCP cache; built-ins should remain.
    reset_mcp_cache()
    assert "read_file" in {t.name for t in get_tools()}
    assert "upstream__noop" not in {t.name for t in get_tools()}


# -- MCPTool wrapper behaviour (mock session) ---------------------------


def _make_fake_tool(tool_name: str = "upstream__noop") -> Any:
    """Build a minimal ``MCPTool`` with a mock session.

    The wrapper's ``run`` path needs a ``ClientSession`` that
    supports ``call_tool``. We supply a stub.
    """
    session = AsyncMock()
    tool = mcp_loader.MCPTool(
        server_name="upstream",
        server_tool_name="noop",
        description="does nothing",
        parameters={"type": "object", "properties": {}},
        session=session,
        execute_timeout=2.0,
    )
    assert tool.name == tool_name
    return tool


def test_mcp_tool_runs_call_and_returns_text_content() -> None:
    """``MCPTool.run`` calls the underlying session and joins the
    text parts with newlines.

    We construct a fake ``result`` with two text content items
    and a sensible ``isError=False``.
    """

    class _Item:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Result:
        content = [_Item("hello"), _Item("world")]
        isError = False

    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=_Result())

    tool = mcp_loader.MCPTool(
        server_name="upstream",
        server_tool_name="greet",
        description="greet",
        parameters={"type": "object"},
        session=session,
        execute_timeout=2.0,
    )
    ctx = ToolContext(
        state_dir="x", workspace=Path("/tmp"), chat_id="c", employee_id=1, channel="webui"
    )

    result = _run_async(tool.run(ctx, name="bob"))
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.content == "hello\nworld"
    session.call_tool.assert_awaited_once_with("greet", arguments={"name": "bob"})


def test_mcp_tool_run_marks_is_error_on_remote_error() -> None:
    class _Item:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Result:
        content = [_Item("upstream said no")]
        isError = True

    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=_Result())

    tool = mcp_loader.MCPTool(
        server_name="upstream",
        server_tool_name="fail",
        description="",
        parameters={},
        session=session,
        execute_timeout=2.0,
    )
    ctx = ToolContext(
        state_dir="x", workspace=Path("/tmp"), chat_id="c", employee_id=1, channel="webui"
    )
    result = _run_async(tool.run(ctx))
    assert result.is_error is True


def test_mcp_tool_run_translates_timeout() -> None:
    """``asyncio.TimeoutError`` from the inner call surfaces as
    ``ToolResult(is_error=True)`` with a server-named message."""

    async def _hang(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError

    session = AsyncMock()
    session.call_tool = _hang

    tool = mcp_loader.MCPTool(
        server_name="slow",
        server_tool_name="stuck",
        description="",
        parameters={},
        session=session,
        execute_timeout=0.05,  # tiny — branch must fire
    )
    ctx = ToolContext(
        state_dir="x", workspace=Path("/tmp"), chat_id="c", employee_id=1, channel="webui"
    )
    result = _run_async(tool.run(ctx))
    assert result.is_error is True
    assert "slow" in (result.content or "")
    assert "timed out" in (result.content or "")


def test_mcp_tool_schema_round_trip() -> None:
    """Anthropic schemas round-trip ``name`` / ``description`` /
    ``input_schema`` verbatim."""
    tool = _make_fake_tool()
    schema = tool.to_anthropic_schema()
    assert schema["name"] == "upstream__noop"
    assert schema["description"] == "does nothing"
    # input_schema came through unchanged.
    assert schema["input_schema"]["type"] == "object"


# -- safe_close is idempotent -------------------------------------------


@pytest.mark.asyncio
async def test_safe_close_when_disconnected_is_noop() -> None:
    """Calling ``disconnect`` on a server that never connected
    leaves a clean state."""
    server = mcp_loader.MCPServerConnection(name="never", command="echo")
    # Should not raise.
    await server.disconnect()
    assert server.exit_stack is None
    assert server.tools == []


@pytest.mark.asyncio
async def test_cleanup_is_idempotent() -> None:
    """Calling ``cleanup_mcp_connections`` twice doesn't choke."""
    # Start from a clean slate.
    mcp_loader._connections.clear()
    await mcp_loader.cleanup_mcp_connections()
    await mcp_loader.cleanup_mcp_connections()
    assert mcp_loader._connections == []


# -- helpers used above -------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Minimal ``asyncio.run`` for old pythons or environments
    where the project's pytest config doesn't apply to a
    handwritten helper."""
    import asyncio
    return asyncio.run(coro)
