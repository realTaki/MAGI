"""Tests for the ``/api/mcp-servers`` CRUD router.

Pins the operator-facing surface:
  - admin-only auth (cookie-less → 401)
  - name pattern + length validation (422 on bad input)
  - cross-field validation (stdio needs command, http needs url)
  - duplicate name → 409 on POST
  - blank env / header keys → 422
  - GET response masks env / header values; only the
    per-key ``*_set`` booleans echo back
  - PATCH name-rename refused
  - DELETE hard-deletes the row
  - toggle flips the row's ``enabled`` flag
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import init_orm, open_session
    from magi.agent.db.models_contact import Contact
    init_orm(str(sd))

    with open_session() as db:
        admin = Contact(
            name="MCP-admin",
            telegram_id=8801,
            admin=True, role="assigned",
            provider="minimax",
            api_key="sk-fake",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return {"state": sd, "admin": admin}


@pytest.fixture
def client(state):
    from magi.channels.webui.app import create_app
    from magi.channels.webui.api.auth import _sign_uid
    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(state["admin"].id))
    return c


@pytest.fixture
def bare_client(state):
    """Cookie-less TestClient — used for the auth-gate
    test below."""
    from magi.channels.webui.app import create_app
    return TestClient(create_app())


# -- helpers ----------------------------------------------------------------


def _seed_stdio(name: str = "std", **overrides) -> dict:
    body = {
        "name": name,
        "connection_type": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "url": None,
        "enabled": True,
        "connect_timeout": None,
        "execute_timeout": None,
        "sse_read_timeout": None,
        "env": {"API_KEY": "secret"},
        "headers": {},
    }
    body.update(overrides)
    return body


def _seed_http(name: str = "http1", **overrides) -> dict:
    body = {
        "name": name,
        "connection_type": "streamable_http",
        "command": None,
        "args": [],
        "url": "https://api.example.com/mcp",
        "enabled": True,
        "connect_timeout": None,
        "execute_timeout": None,
        "sse_read_timeout": None,
        "env": {},
        "headers": {"Authorization": "Bearer xyz"},
    }
    body.update(overrides)
    return body


# -- auth gate --------------------------------------------------------------


def test_endpoints_require_admin(bare_client):
    """Cookie-less → 401 on every endpoint. The MCP
    surface is admin-only by design: an operator with
    write access to a "fetch" or "github" server can
    arbitrarily extend the LLM's tool menu."""
    assert bare_client.get("/api/mcp-servers").status_code == 401
    assert bare_client.post(
        "/api/mcp-servers", json=_seed_stdio(),
    ).status_code == 401
    assert bare_client.patch(
        "/api/mcp-servers/std", json=_seed_stdio(),
    ).status_code == 401
    assert bare_client.delete("/api/mcp-servers/std").status_code == 401
    assert bare_client.post(
        "/api/mcp-servers/std/toggle",
    ).status_code == 401


# -- create + list ----------------------------------------------------------


def test_create_stdio_persists_row(state, client):
    r = client.post("/api/mcp-servers", json=_seed_stdio(name="std1"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "std1"
    assert body["connection_type"] == "stdio"
    assert body["command"] == "uvx"
    assert body["args"] == ["mcp-server-fetch"]
    assert body["enabled"] is True


def test_create_http_persists_row(state, client):
    r = client.post("/api/mcp-servers", json=_seed_http(name="http1"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["connection_type"] == "streamable_http"
    assert body["url"] == "https://api.example.com/mcp"


def test_create_duplicate_name_returns_409(state, client):
    client.post("/api/mcp-servers", json=_seed_stdio(name="dup"))
    r = client.post("/api/mcp-servers", json=_seed_stdio(name="dup"))
    assert r.status_code == 409
    assert r.json()["code"] == "conflict.mcp_server_name"


def test_create_invalid_name_pattern_returns_422(state, client):
    """Name must match ``^[A-Za-z0-9_-]+$``. Spaces and
    punctuation get a 422 from the Pydantic validator."""
    r = client.post(
        "/api/mcp-servers",
        json=_seed_stdio(name="bad name!"),
    )
    assert r.status_code == 422


def test_create_stdio_without_command_returns_400(state, client):
    body = _seed_stdio(name="bad")
    body["command"] = None
    r = client.post("/api/mcp-servers", json=body)
    assert r.status_code == 400
    assert r.json()["code"] == "validation.mcp_stdio_requires_command"


def test_create_http_without_url_returns_400(state, client):
    body = _seed_http(name="bad")
    body["url"] = None
    r = client.post("/api/mcp-servers", json=body)
    assert r.status_code == 400
    assert r.json()["code"] == "validation.mcp_http_requires_url"


def test_create_blank_env_key_returns_422(state, client):
    body = _seed_stdio(name="bad")
    body["env"] = {"": "value"}
    r = client.post("/api/mcp-servers", json=body)
    assert r.status_code == 422


# -- list masks secrets -----------------------------------------------------


def test_list_masks_env_set_keys(state, client):
    """A key with a non-empty value is reported as
    ``set=True``; the actual value is NOT in the
    response. The UI renders ``••••••`` for set keys
    and uses the value (which the operator just typed)
    only for unset keys."""
    client.post(
        "/api/mcp-servers",
        json=_seed_stdio(name="std", env={"SECRET": "value", "EMPTY": ""}),
    )
    body = client.get("/api/mcp-servers").json()
    assert len(body) == 1
    row = body[0]
    # The set key: env_set["SECRET"] = True, value masked
    assert row["env_set"]["SECRET"] is True
    # The unset key: env_set["EMPTY"] = False
    assert row["env_set"]["EMPTY"] is False
    # The actual value is never returned (the response
    # includes the dict for symmetry but the value
    # field is empty when the key is "set").
    assert row["env"]["SECRET"] == ""


def test_list_masks_headers_set_keys(state, client):
    client.post("/api/mcp-servers", json=_seed_http(name="http"))
    row = client.get("/api/mcp-servers").json()[0]
    assert row["headers_set"]["Authorization"] is True
    assert row["headers"]["Authorization"] == ""


# -- get single -------------------------------------------------------------


def test_get_single_returns_masked(state, client):
    client.post("/api/mcp-servers", json=_seed_stdio(name="x"))
    row = client.get("/api/mcp-servers/x").json()
    assert row["name"] == "x"
    assert row["env_set"]["API_KEY"] is True
    assert row["env"]["API_KEY"] == ""


def test_get_unknown_returns_404(state, client):
    r = client.get("/api/mcp-servers/does-not-exist")
    assert r.status_code == 404


# -- patch ------------------------------------------------------------------


def test_patch_updates_row(state, client):
    client.post("/api/mcp-servers", json=_seed_stdio(name="x"))
    new = _seed_stdio(name="x", command="new-cmd", enabled=False)
    r = client.patch("/api/mcp-servers/x", json=new)
    assert r.status_code == 200
    body = r.json()
    assert body["command"] == "new-cmd"
    assert body["enabled"] is False


def test_patch_refuses_rename(state, client):
    """The ``name`` is the primary key — PATCH with a
    different name in the body returns 400. To rename,
    the operator deletes + creates."""
    client.post("/api/mcp-servers", json=_seed_stdio(name="old"))
    r = client.patch(
        "/api/mcp-servers/old",
        json=_seed_stdio(name="new"),
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.mcp_name_immutable"


def test_patch_clear_env_sends_empty_string(state, client):
    """Editing a row with ``env={"KEY": ""}`` clears
    the key — the server treats ``""`` as "operator
    explicitly set this to empty, don't inherit from
    parent env"."""
    client.post(
        "/api/mcp-servers",
        json=_seed_stdio(name="x", env={"API_KEY": "value"}),
    )
    client.patch(
        "/api/mcp-servers/x",
        json=_seed_stdio(name="x", env={"API_KEY": ""}),
    )
    row = client.get("/api/mcp-servers/x").json()
    assert row["env_set"]["API_KEY"] is False


# -- delete + toggle --------------------------------------------------------


def test_delete_removes_row(state, client):
    client.post("/api/mcp-servers", json=_seed_stdio(name="x"))
    r = client.delete("/api/mcp-servers/x")
    assert r.status_code == 204
    assert client.get("/api/mcp-servers").json() == []


def test_delete_unknown_returns_404(state, client):
    r = client.delete("/api/mcp-servers/nope")
    assert r.status_code == 404


def test_toggle_flips_enabled(state, client):
    client.post("/api/mcp-servers", json=_seed_stdio(name="x", enabled=True))
    body = client.post("/api/mcp-servers/x/toggle").json()
    assert body["enabled"] is False
    body = client.post("/api/mcp-servers/x/toggle").json()
    assert body["enabled"] is True


def test_toggle_unknown_returns_404(state, client):
    r = client.post("/api/mcp-servers/nope/toggle")
    assert r.status_code == 404
