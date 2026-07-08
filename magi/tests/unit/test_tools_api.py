"""End-to-end TestClient test for ``/api/tools``.

The endpoint reads :mod:`magi.agent.tools.registry` directly,
so it doesn't need a real LLM, just the App + a seeded admin
cookie. The MCP-loader branch is skipped via
``bootstrap_mcp_tools``'s graceful "no mcp.json" fallback
(see ``test_mcp_loader`` for the explicit coverage of that
loader).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi.agent.tools import registry as registry_mod
from magi.agent.tools.base import Tool, ToolContext, ToolResult


class _FakeTool(Tool):
    name = "fake__demo"
    description = "Demo tool for /api/tools coverage. Takes a name."
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
        },
    }

    async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
        return ToolResult(content="")


class _LongTool(Tool):
    name = "fake__long"
    description = "x" * 500
    input_schema: dict = {}

    async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
        return ToolResult(content="")


class _McpTool(Tool):
    name = "github__create_issue"
    description = "Create a GitHub issue."
    input_schema: dict = {}

    async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
        return ToolResult(content="")


class _AardvarkTool(Tool):
    name = "fake__aardvark"
    description = "x"
    input_schema: dict = {}

    async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
        return ToolResult(content="")


class _ZebraTool(Tool):
    name = "fake__zebra"
    description = "y"
    input_schema: dict = {}

    async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
        return ToolResult(content="")


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal state_dir + admin Employee for App startup.

    The route's ``AdminGate`` looks up the cookie's chat_id
    in the ``employees`` table; we seed one so the gate
    lets the test through.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    from magi.agent.db import init_sqlite
    from magi.agent.db import Employee, init_orm, open_session
    import magi.agent.db.engine as _orm_mod
    # Reset the SQLAlchemy engine singleton so each test
    # opens its own fresh sqlite file. Without this, an
    # earlier test's engine (pointing at a deleted path)
    # serves subsequent tests and integrity errors fly.
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None

    init_sqlite(str(sd))
    init_orm(str(sd))
    with open_session() as s:
        s.add(
            Employee(
                name="Test Admin (tools)",
                telegram_id=9001,
                role="admin",
                provider="minimax",
                api_key="fake-key-for-tests",
            )
        )
        s.commit()
    return sd


@pytest.fixture
def client(state) -> TestClient:
    # Each test starts with a clean tool cache and may
    # rewrite it below. The autouse``_reset_tool_cache``
    # fixture enforces isolation.
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "9001")
    return c


@pytest.fixture(autouse=True)
def _reset_tool_cache() -> None:
    """Process-global cache → known state per test.

    The cache survives across tests because it lives on the
    registry module. If we don't reset, the order in which
    tests run decides what each case sees — fine when pytest
    runs them in declaration order, less fine when re-runs
    with --randomly-seeded ordering (or in the future when
    someone adds new cases).
    """
    registry_mod._tools_cache = [_FakeTool()]
    registry_mod._mcp_tools_cache = None
    yield
    # Teardown: clear so the next test starts clean (the
    # _reset_tool_cache fixture at the top of the next test
    # will re-seed).
    registry_mod._tools_cache = None
    registry_mod._mcp_tools_cache = None


def test_tools_lists_builtin_schemas(client: TestClient) -> None:
    """Round-trip: GET /api/tools returns the fake tool."""
    r = client.get("/api/tools")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "fake__demo"
    # Description is collapsed to a single line.
    assert "\n" not in body["items"][0]["description"]
    # ``prop_count`` counts the input_schema's properties dict.
    assert body["items"][0]["prop_count"] == 2


def test_tools_description_truncated_at_200_chars(client: TestClient) -> None:
    """Long descriptions get a ``...`` suffix at 200 chars."""
    registry_mod._tools_cache = [_LongTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["name"] == "fake__long"
    desc = body["items"][0]["description"]
    assert len(desc) == 200
    assert desc.endswith("...")


def test_tools_includes_mcp_cache_too(client: TestClient) -> None:
    """``get_tool_schemas`` glues built-in + MCP into one
    response. Pin that behaviour."""

    # Built-in stays from the autouse fixture (resets at
    # teardown); we add an MCP cache entry on top.
    registry_mod._mcp_tools_cache = [_McpTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    names = {it["name"] for it in body["items"]}
    # Both the fake builtin + the fake MCP tool come back.
    assert "fake__demo" in names
    assert "github__create_issue" in names


def test_tools_response_is_sorted_by_name(client: TestClient) -> None:
    """Stable lexicographic order so the dashboard's table
    renders deterministically across refreshes."""

    registry_mod._tools_cache = [_ZebraTool(), _AardvarkTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    names = [it["name"] for it in body["items"]]
    assert names == sorted(names) == ["fake__aardvark", "fake__zebra"]
