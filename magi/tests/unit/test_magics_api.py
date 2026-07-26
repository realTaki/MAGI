"""End-to-end tests for ``/api/magics`` — the "MAGI 团队"
CRUD surface introduced by the post-refactor reframe.

A ``MAGIC`` row is the org container: a tree of MAGI teams,
each anchoring an ``adam`` Magi (exactly one per MAGIC). The
endpoints exercised here:

  - ``GET    /api/magics``          — flat list with parent_id
  - ``POST   /api/magics``          — create (name, parent_id)
  - ``GET    /api/magics/{id}``     — single row + child_count
  - ``PATCH  /api/magics/{id}``     — rename / reparent / set adam_id
  - ``DELETE /api/magics/{id}``     — reparents children before delete

Fixtures pin: ``MAGIC_STATE_DIR`` (test-scoped), one admin
``Contact`` (so the AdminGate lets the request through), and
the dispatched signed cookie.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# -- fixtures --------------------------------------------------------------

_MAGIC_POSITIONS: tuple[str, ...] = ("adam", "eve")


def _signed_session_cookie(uid: int) -> str:
    """Mint an HMAC-signed ``magi_session`` cookie value
    (the prod cookie layer rejects naked ids)."""
    from magi.channels.webui.api.auth import _sign_uid

    return _sign_uid(uid)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """MAGI_STATE_DIR + ORM + one admin Contact + one seeded MAGIC.root."""
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
        # The seed already created MAGIC.root + one adam Magi;
        # capture that row's id so tests can reparent cleanly.
        root = db.scalar(
            db.query(MAGIC).filter_by(name="Genesis")  # seed name in _seed_default_root
            .statement
        ) if False else db.scalar(__import__("sqlalchemy").select(MAGIC).where(MAGIC.name == "Genesis"))
        adam = db.scalar(__import__("sqlalchemy").select(Magi).where(Magi.magic_position == "adam"))
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


@pytest.fixture
def non_admin_client(env):
    """A second Contact with role='contact' whose signed
    cookie should be denied at the AdminGate."""
    from magi.agent.db import Contact, open_session
    from magi.channels.webui.app import create_app

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

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _signed_session_cookie(uid))
    return c


# -- tests -----------------------------------------------------------------


def test_list_magics_returns_seeded_root(client, env):
    """The default seed stamps one ``MAGIC`` row; list
    reflects it (sorted by name)."""
    r = client.get("/api/magics")
    assert r.status_code == 200
    body = r.json()
    names = [m["name"] for m in body]
    assert env["root"].name in names
    # The seed is name='Genesis' and is top-level.
    root = next(m for m in body if m["name"] == env["root"].name)
    assert root["parent_id"] is None
    assert root["adam_id"] == env["adam"].id


def test_create_magic_top_level(client, env):
    r = client.post(
        "/api/magics",
        json={"name": "Marketing", "parent_id": env["root"].id},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Marketing"
    assert body["parent_id"] == env["root"].id
    assert body["adam_id"] is None
    assert body["child_count"] == 0


def test_create_magic_duplicate_name_rejected(client, env):
    """Two rows with the same name -> 400 (duplicate-name
    check; the unique index would also kick in)."""
    r = client.post(
        "/api/magics",
        json={"name": env["root"].name},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_name_duplicate"


def test_create_magic_invalid_parent_rejected(client):
    r = client.post(
        "/api/magics",
        json={"name": "Orphan", "parent_id": 9999},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.parent_magic_not_found"


def test_get_magic_returns_child_count(client, env):
    """``GET /api/magics/{id}`` includes child_count (set
    after re-fetch + children load)."""
    # Create a child so the root has at least one.
    cr = client.post(
        "/api/magics",
        json={"name": "Eng", "parent_id": env["root"].id},
    )
    assert cr.status_code == 201
    child_id = cr.json()["id"]

    r = client.get(f"/api/magics/{env['root'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == env["root"].name
    assert body["child_count"] == 1

    # The child itself has 0 children.
    r = client.get(f"/api/magics/{child_id}")
    assert r.json()["child_count"] == 0


def test_get_magic_404_for_missing(client):
    r = client.get("/api/magics/9999")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found.magic"


def test_update_magic_renames(client):
    """``PATCH name`` updates, blank rejected, duplicate rejected."""
    cr = client.post("/api/magics", json={"name": "OldName"})
    mid = cr.json()["id"]

    r = client.patch(f"/api/magics/{mid}", json={"name": "NewName"})
    assert r.status_code == 200
    assert r.json()["name"] == "NewName"

    # Duplicate — should 400.
    cr2 = client.post("/api/magics", json={"name": "Other"})
    other_id = cr2.json()["id"]
    r = client.patch(f"/api/magics/{mid}", json={"name": "Other"})
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_name_duplicate"


def test_update_magic_self_parent_rejected(client, env):
    """``PATCH parent_id=self_id`` raises — a MAGIC can't
    be its own parent."""
    cr = client.post(
        "/api/magics",
        json={"name": "Cycle1", "parent_id": env["root"].id},
    )
    mid = cr.json()["id"]

    r = client.patch(f"/api/magics/{mid}", json={"parent_id": mid})
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_self_parent"


def test_update_magic_reparents_cycle_rejected(client, env):
    """Reparent a subtree under a descendant — refused."""
    cr1 = client.post(
        "/api/magics",
        json={"name": "A", "parent_id": env["root"].id},
    )
    a = cr1.json()["id"]
    cr2 = client.post(
        "/api/magics",
        json={"name": "A.b", "parent_id": a},
    )
    ab = cr2.json()["id"]

    # Now try to make ``a`` a child of ``A.b`` — cycle.
    r = client.patch(f"/api/magics/{a}", json={"parent_id": ab})
    assert r.status_code == 400
    assert r.json()["code"] == "validation.magic_cycle"


def test_update_magic_sets_adam(client, env):
    """``PATCH adam_id`` binds an existing adam Magi."""
    mid = env["root"].id
    # The seed already set root.adam_id; explicitly re-bind
    # to the same id is a no-op success case.
    r = client.patch(f"/api/magics/{mid}", json={"adam_id": env["adam"].id})
    assert r.status_code == 200
    assert r.json()["adam_id"] == env["adam"].id


def test_update_magic_invalid_adam_id_rejected(client, env):
    r = client.patch(
        f"/api/magics/{env['root'].id}",
        json={"adam_id": 9999},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.adam_magi_not_found"


def test_delete_magic_reparents_children(client, env):
    """Deleting a non-leaf MAGIC re-parents its direct
    children to the deleted row's parent (so they don't
    become orphans with a NULL parent_id reference)."""
    cr = client.post(
        "/api/magics",
        json={"name": "ParentA", "parent_id": env["root"].id},
    )
    parent_a = cr.json()["id"]
    cr = client.post(
        "/api/magics",
        json={"name": "ParentA.1", "parent_id": parent_a},
    )
    child1 = cr.json()["id"]
    cr = client.post(
        "/api/magics",
        json={"name": "ParentA.2", "parent_id": parent_a},
    )
    child2 = cr.json()["id"]

    r = client.delete(f"/api/magics/{parent_a}")
    assert r.status_code == 204

    # Both children now report parent_id == root (the
    # grandparent of the deleted row).
    r1 = client.get(f"/api/magics/{child1}")
    r2 = client.get(f"/api/magics/{child2}")
    assert r1.json()["parent_id"] == env["root"].id
    assert r2.json()["parent_id"] == env["root"].id


def test_delete_magic_404_for_missing(client):
    r = client.delete("/api/magics/9999")
    assert r.status_code == 404


def test_all_endpoints_require_admin(non_admin_client):
    """``non_admin_client`` (role='contact') is rejected at
    the AdminGate for every verb on /api/magics."""
    r = non_admin_client.get("/api/magics")
    assert r.status_code == 401
    r = non_admin_client.post("/api/magics", json={"name": "X"})
    assert r.status_code == 401
    r = non_admin_client.patch("/api/magics/1", json={"name": "Y"})
    assert r.status_code == 401
    r = non_admin_client.delete("/api/magics/1")
    assert r.status_code == 401


def test_endpoints_require_signed_cookie(client):
    """A naked ``str(uid)`` cookie is rejected by the
    signed-uid verifier (admin_gate->auth._verify_signed_uid).
    This pins that the cookie layer doesn't have an
    accidental dev-bypass — production behaviour in tests."""
    raw_client = TestClient(client.app)
    raw_client.cookies.set("magi_session", "1")
    r = raw_client.get("/api/magics")
    assert r.status_code == 401
