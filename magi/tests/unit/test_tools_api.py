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
    c.cookies.set("magi_session", "1")
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



class _GatedTool(Tool):
    """Tool with a non-empty :attr:`ALLOWED_ROLES` for the
    read-only audit surface. Declared as a class
    attribute (rather than reusing e.g. AddActionItemTool)
    so this test isn't coupled to a specific implementation
    choice — the endpoint contract is what we want to lock.
    """

    name = "fake__gated"
    description = "Tool with a declared ALLOWED_ROLES set."
    input_schema: dict = {}
    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
        return ToolResult(content="")


def test_tools_response_includes_allowed_roles(client: TestClient) -> None:
    """``GET /api/tools`` surfaces each tool's
    :attr:`ALLOWED_ROLES` as a sorted list — the
    dashboard's "allowed roles" column renders directly
    from this field."""
    registry_mod._tools_cache = [_GatedTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["name"] == "fake__gated"
    # Sorted: roles render in a stable order regardless of
    # frozenset ordering.
    assert body["items"][0]["allowed_roles"] == ["admin", "assigned"]


def test_tools_response_returns_empty_list_for_unrestricted(
    client: TestClient,
) -> None:
    """Tools that declare no role restriction (the default
    ``ALLOWED_ROLES = frozenset()``) come back with an
    empty list — the dashboard renders "all roles" for
    those, distinct from a non-empty allowed set."""
    # ``_FakeTool`` doesn't declare ALLOWED_ROLES, so it
    # inherits the empty frozenset from :class:`Tool`.
    registry_mod._tools_cache = [_FakeTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["name"] == "fake__demo"
    assert body["items"][0]["allowed_roles"] == []


def test_tools_response_keeps_role_order_alphabetical(
    client: TestClient,
) -> None:
    """``ALLOWED_ROLES`` is a set — without an explicit
    sort, two servers could render the same set in
    different orders. The endpoint sorts so the dashboard
    table layout is byte-stable."""

    class _ReversedGatedTool(Tool):
        name = "fake__rev"
        description = "x"
        input_schema: dict = {}
        # Declared in reverse alphabetical so the test
        # would FAIL if the sort step were skipped.
        ALLOWED_ROLES = frozenset({"zzz", "admin", "middle"})

        async def run(self, ctx: ToolContext, **kwargs):  # pragma: no cover
            return ToolResult(content="")

    registry_mod._tools_cache = [_ReversedGatedTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["allowed_roles"] == ["admin", "middle", "zzz"]


def test_tools_response_calls_get_tools_grouped_with_no_role_filter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dashboard view is informational, not gated. The
    endpoint explicitly passes ``caller_role=None`` so the
    full registry surfaces — even tools whose
    :attr:`ALLOWED_ROLES` would otherwise strip them from
    a role-filtered view.

    The endpoint uses :func:`get_tools_grouped` (the
    (builtin, mcp) tuple helper) rather than :func:`get_tools`
    — the spy patches the helper the route actually calls.
    """
    from magi.channels.webui.api import tools as tools_api
    seen: list = []
    real = tools_api.get_tools_grouped

    def spy(caller_role=None):  # type: ignore[no-untyped-def]
        seen.append(caller_role)
        return real(caller_role=caller_role)

    monkeypatch.setattr(tools_api, "get_tools_grouped", spy)
    r = client.get("/api/tools")
    assert r.status_code == 200
    assert seen == [None], (
        "audit endpoint must call get_tools_grouped(caller_role=None) "
        "so the full registry surfaces; gating here would "
        "strip admin-only tools from the operator's view"
    )



def test_tools_response_tags_builtins_as_builtin(
    client: TestClient,
) -> None:
    """Built-in tools come back with ``source="builtin"`` —
    the dashboard renders them in the dedicated card."""
    registry_mod._tools_cache = [_FakeTool()]
    registry_mod._mcp_tools_cache = None
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["source"] == "builtin"


def test_tools_response_tags_mcp_tools_as_mcp(
    client: TestClient,
) -> None:
    """MCP tools come back with ``source="mcp"``."""
    registry_mod._tools_cache = []
    registry_mod._mcp_tools_cache = [_McpTool()]
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["source"] == "mcp"
    assert body["items"][0]["name"] == "github__create_issue"


def test_tools_response_partitions_by_source(
    client: TestClient,
) -> None:
    """Mixed registry — built-in first, MCP second,
    each correctly tagged. The dashboard's filter groups
    client-side by ``source``; this pins the wire shape."""
    registry_mod._tools_cache = [_FakeTool()]
    registry_mod._mcp_tools_cache = [_McpTool()]

    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    pairs = sorted(
        (it["source"], it["name"]) for it in body["items"]
    )
    # Name-sorted within the flat list — the dashboard's
    # client-side ``source`` groupBy produces two cards.
    assert pairs == [
        ("builtin", "fake__demo"),
        ("mcp", "github__create_issue"),
    ]


def test_tools_response_default_source_field_present(client: TestClient) -> None:
    """Regression guard for the ``source`` field — older
    clients (pre-split) wouldn't see it, so the schema
    guarantees it from now on."""
    registry_mod._tools_cache = [_FakeTool()]
    r = client.get("/api/tools")
    body = r.json()
    assert "source" in body["items"][0]
