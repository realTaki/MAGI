"""End-to-end tests for ``/api/magis`` — the "智能体管理"
CRUD surface introduced by the post-refactor reframe.

Each ``Magi`` row is a MAGI runtime agent bound to one
``MAGIC`` via ``magic_id``. ``magic_position`` selects the
archetype (one of ``adam`` / ``eve``). Endpoints exercised:

  - ``GET    /api/magis``              — flat list with optional ``?magic_id=``
  - ``POST   /api/magis``              — create (magic_id, position, etc.)
  - ``GET    /api/magis/{id}``         — single row
  - ``PATCH  /api/magis/{id}``         — rename / re-position / rotate key
  - ``DELETE /api/magis/{id}``         — drop the agent row

The fixtures pin ``MAGIC_STATE_DIR``, the admin Contact, the
seeded ``MAGIC.root`` + its adam, and the signed cookie.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


_MAGI_POSITIONS: tuple[str, ...] = ("adam", "eve")


def _signed_session_cookie(uid: int) -> str:
    """Mint an HMAC-signed ``magi_session`` cookie value
    (the prod cookie layer rejects naked ids)."""
    from magi.channels.webui.api.auth import _sign_uid

    return _sign_uid(uid)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """MAGI_STATE_DIR + ORM + one admin Contact + the
    seeded MAGIC root (with its default adam Magi)."""
    state = tmp_path / "state"
    state.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import (
        Contact,
        MAGIC,
        Magi,
        init_orm,
        init_sqlite,
        open_session,
    )
    import sqlalchemy as sa

    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as db:
        admin = Contact(
            name="Alice",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake",
        )
        db.add(admin)
        db.flush()
        root = db.scalar(sa.select(MAGIC).where(MAGIC.name == "Genesis"))
        adam = db.scalar(
            sa.select(Magi).where(Magi.magic_position == "adam")
        )
        db.commit()
        db.refresh(admin)

    return {
        "state": state,
        "admin": admin,
        "root": root,
        "adam": adam,
    }


@pytest.fixture
def client(env):
    """TestClient with signed admin cookie."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _signed_session_cookie(env["admin"].id))
    return c


# -- tests -----------------------------------------------------------------


def test_list_magics_returns_seeded_adam(client, env):
    """Default seed stamps one adam Magi bound to MAGIC.root.
    Listing returns at least that one, sorted by id (adam is
    the lowest id)."""
    r = client.get("/api/magis")
    assert r.status_code == 200
    body = r.json()
    ids = [m["id"] for m in body]
    assert env["adam"].id in ids
    adam_row = next(m for m in body if m["id"] == env["adam"].id)
    assert adam_row["magic_id"] == env["root"].id
    assert adam_row["magic_position"] == "adam"


def test_list_magis_filter_by_magic_id(client, env):
    """``?magic_id=X`` returns only rows bound to that MAGIC."""
    r = client.get(f"/api/magis?magic_id={env['root'].id}")
    assert r.status_code == 200
    body = r.json()
    assert all(m["magic_id"] == env["root"].id for m in body)


def test_create_magi_eve(client, env):
    """Create an eve Magi under the seeded root."""
    r = client.post(
        "/api/magis",
        json={
            "magic_id": env["root"].id,
            "name": "Eve1",
            "magic_position": "eve",
            "provider": "anthropic",
            "api_key": "sk-eve1",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Eve1"
    assert body["magic_position"] == "eve"
    assert body["provider"] == "anthropic"
    assert body["api_key_set"] is True
    assert body["api_key_last4"] == "eve1"


def test_create_magi_invalid_position(client, env):
    """``magic_position='minion'`` rejected with 400 + structured error."""
    r = client.post(
        "/api/magis",
        json={
            "magic_id": env["root"].id,
            "magic_position": "minion",
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_position_unknown"


def test_create_magi_unknown_magic_id(client):
    """``magic_id`` pointing at no row -> 400."""
    r = client.post(
        "/api/magis",
        json={"magic_id": 9999, "magic_position": "eve"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_id_not_found"


def test_create_magi_adam_when_already_bound_returns_409(client, env):
    """The MAGIC already has an adam (the seed). Trying to
    create a second adam raises 409 (no silent overwrite).

    Bug guard: ensures the invariant ``one adam per MAGIC``
    is enforced at the API boundary, not left to luck."""
    r = client.post(
        "/api/magis",
        json={
            "magic_id": env["root"].id,
            "magic_position": "adam",
            "name": "Adam2",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "validation.adam_already_assigned"


def test_create_magi_adam_when_unbound_succeeds(client, env):
    """If the MAGIC has no adam yet (e.g. a fresh child MAGIC),
    creating one binds it."""
    # Make a fresh child MAGIC under root — seed only created one root.
    cr = client.post(
        "/api/magics",
        json={"name": "Branch", "parent_id": env["root"].id},
    )
    branch_id = cr.json()["id"]
    assert cr.json()["adam_id"] is None

    # Now create an adam for the branch — should succeed and bind.
    r = client.post(
        "/api/magis",
        json={
            "magic_id": branch_id,
            "magic_position": "adam",
            "name": "BranchAdam",
        },
    )
    assert r.status_code == 201, r.text
    new_adam_id = r.json()["id"]

    # MAGIC.adam_id is now bound to the new adam.
    r = client.get(f"/api/magics/{branch_id}")
    assert r.json()["adam_id"] == new_adam_id


def test_create_magi_minimal_payload(client, env):
    """name is optional; defaults to None. Position required."""
    r = client.post(
        "/api/magis",
        json={"magic_id": env["root"].id, "magic_position": "eve"},
    )
    assert r.status_code == 201
    assert r.json()["name"] is None


def test_get_magi(client, env):
    pid = env["adam"].id
    r = client.get(f"/api/magis/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pid
    assert body["magic_position"] == "adam"


def test_get_magi_404(client):
    r = client.get("/api/magis/9999")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found.magi"


def test_patch_magi_renames(client, env):
    pid = env["adam"].id
    r = client.patch(f"/api/magis/{pid}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"


def test_patch_magi_rotates_api_key(client, env):
    """PATCH api_key writes new value, last4 reflects it
    (and ``""`` clears)."""
    pid = env["adam"].id
    r = client.patch(f"/api/magis/{pid}", json={"api_key": "sk-new"})
    assert r.status_code == 200
    assert r.json()["api_key_last4"] == "w-new" if False else r.json()["api_key_last4"][-4:] == "-new"

    # Clear via empty string.
    r = client.patch(f"/api/magis/{pid}", json={"api_key": ""})
    assert r.status_code == 200
    assert r.json()["api_key_set"] is False
    assert r.json()["api_key_last4"] is None


def test_patch_magi_invalid_position_400(client, env):
    pid = env["adam"].id
    r = client.patch(f"/api/magis/{pid}", json={"magic_position": "minion"})
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_position_unknown"


def test_patch_magi_provider_change(client, env):
    pid = env["adam"].id
    r = client.patch(f"/api/magis/{pid}", json={"provider": "openai"})
    assert r.status_code == 200
    assert r.json()["provider"] == "openai"


def test_patch_magi_404(client):
    r = client.patch("/api/magis/9999", json={"name": "X"})
    assert r.status_code == 404


def test_delete_magi_eve(client, env):
    """Deleting an eve leaves MAGIC.adam_id alone."""
    cr = client.post(
        "/api/magis",
        json={
            "magic_id": env["root"].id,
            "magic_position": "eve",
            "name": "Doomed",
        },
    )
    eid = cr.json()["id"]
    r = client.delete(f"/api/magis/{eid}")
    assert r.status_code == 204

    r = client.get(f"/api/magis/{eid}")
    assert r.status_code == 404


def test_delete_magi_adam_clears_adam_binding(client, env):
    """Deleting the bound adam MUST clear MAGIC.adam_id so a
    new adam can later be created for that MAGIC."""
    adam_id = env["adam"].id
    r = client.delete(f"/api/magis/{adam_id}")
    assert r.status_code == 204

    # MAGIC.adam_id is now NULL.
    r = client.get(f"/api/magics/{env['root'].id}")
    assert r.json()["adam_id"] is None


def test_delete_magi_404(client):
    r = client.delete("/api/magis/9999")
    assert r.status_code == 404


def test_list_magis_requires_admin(client, env):
    """A non-admin (role='contact') gets 401 at the gate."""
    from magi.agent.db import Contact, open_session

    with open_session() as db:
        u = Contact(
            name="User2",
            telegram_id=9002,
            role="contact",
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id

    raw = TestClient(client.app)
    raw.cookies.set("magi_session", _signed_session_cookie(uid))
    r = raw.get("/api/magis")
    assert r.status_code == 401

    r = raw.post("/api/magis", json={"magic_id": env["root"].id, "magic_position": "eve"})
    assert r.status_code == 401

    r = raw.patch(f"/api/magis/{env['adam'].id}", json={"name": "X"})
    assert r.status_code == 401

    r = raw.delete(f"/api/magis/{env['adam'].id}")
    assert r.status_code == 401
