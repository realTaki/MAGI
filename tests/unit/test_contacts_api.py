"""End-to-end tests for ``/api/contacts`` — the unified people
directory surface (D.25, post-refactor).

``Contact`` is the merged Employees + ContactEntry + Person table
that the post-refactor reframe collapsed everything into. This
test pins the *current* ``GET / POST / PATCH`` contract:

  1. **Auth gate** — ``AdminGate`` (cookie admin or 401).
  2. **CRUD shape** — name/role/admin/telegram_id
     reads round-trip through PATCH.
  3. **Scopes** — ``role``, ``with_notes``, ``separated``,
     ``include_separated`` filters work without false
     positives between them.
  4. **Soft delete** — ``PATCH separated=true`` stamps
     ``separated_at``; ``PATCH separated=false`` restores.
  5. **Validation** — name required, role enum, telegram_id
     uniqueness.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# -- fixtures --------------------------------------------------------------

_CONTACT_ROLES: tuple[str, ...] = ("admin", "assigned", "contact", "guest")

@pytest.fixture
def env(monkeypatch, tmp_path):
    """MAGI_STATE_DIR + ORM + one admin Contact + one regular Contact."""
    state = tmp_path / "state"
    state.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    
    import magi.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.db import (
        Contact,
        init_orm,
        init_sqlite,
        open_session,
    )
    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as db:
        alice = Contact(
            name="Alice",
            display_name="ali",
            telegram_id=9001,
            admin=True, role="assigned",
        )
        bob = Contact(
            name="Bob",
            telegram_id=9002,
            role="assigned",
        )
        charlie = Contact(
            name="Charlie",
            telegram_id=9003,
            role='guest',
        )
        db.add_all([alice, bob, charlie])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)
        db.refresh(charlie)

    return {"state": state, "alice": alice, "bob": bob, "charlie": charlie}

def _signed_session_cookie(uid: int) -> str:
    """Return an HMAC-signed ``magi_session`` value for
    ``uid`` (the value the prod cookie layer uses).

    D.24: cookie carries ``Contact.id`` (an int) but the
    server-side gate (:func:`auth_gates.admin_gate`) calls
    ``auth._verify_signed_uid`` on it, which requires a
    ``uid:ts:hmac`` triple. A naked ``str(uid)`` is rejected,
    so test fixtures must mint a real signed token.

    We rely on ``init_orm`` having already set
    ``MAGI_STATE_DIR`` (so ``_signing_key`` derives a stable
    key per test).
    """
    from magi.channels.api.auth import _sign_uid

    return _sign_uid(uid)

@pytest.fixture
def client(env):
    """TestClient with Alice's signed cookie (admin)."""
    from magi.channels.api.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _signed_session_cookie(env["alice"].id))
    return c

@pytest.fixture
def charlie_client(env):
    """TestClient with Charlie's signed cookie (role='guest',
    not admin). Used to verify AdminGate rejects non-admin
    callers."""
    from magi.channels.api.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _signed_session_cookie(env["charlie"].id))
    return c

# -- tests -----------------------------------------------------------------

def test_list_contacts_returns_empty_when_no_rows(client):
    """Pristine DB (no seeded contacts would happen here —
    but verify that calling before Alice's send-side hasn't
    materialised any other rows still serves a clean shape.

    In this test Alice already exists, but a fresh DB filter
    is what we'd hit if Alice's role wasn't admin — that
    path is covered by the next tests."""
    r = client.get("/api/contacts")
    assert r.status_code == 200
    body = r.json()
    # Alice + Bob + Charlie seeded; name ASC.
    assert body["total"] == 3
    names = [it["name"] for it in body["items"]]
    assert names == sorted(names)
    assert names[0] == "Alice"

def test_list_contacts_filters_by_admin(client, env):
    """``?admin=true`` returns only WebUI operators (Alice).

    After the role/admin split (2024), ``role='admin'`` is
    no longer a valid value — WebUI sign-in rights are
    carried by the separate ``admin`` boolean. The
    ``/api/contacts?admin=true`` filter is the canonical
    way to list operators; the old ``?role=admin`` query
    now returns 400 ``validation.role_unknown``."""
    r = client.get("/api/contacts?admin=true")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Alice"
    assert body["items"][0]["admin"] is True

def test_list_contacts_role_validation(client):
    """Unknown role -> 400."""
    r = client.get("/api/contacts?role=minion")
    assert r.status_code == 400
    assert r.json()["code"] == "validation.role_unknown"

def test_list_contacts_hides_separated_by_default(client, env):
    """Default scope hides separated contacts."""
    # Soft-delete Bob.
    pid = env["bob"].id
    r = client.patch(f"/api/contacts/{pid}", json={"separated": True})
    assert r.status_code == 200
    assert r.json()["separated_at"] is not None

    # Default GET: Bob hidden, only Alice + Charlie visible.
    r = client.get("/api/contacts")
    body = r.json()
    assert body["total"] == 2
    assert {it["name"] for it in body["items"]} == {"Alice", "Charlie"}

def test_list_contacts_separated_scope(client, env):
    """``?separated=true`` shows only separated rows (Bob after soft-delete)."""
    pid = env["bob"].id
    client.patch(f"/api/contacts/{pid}", json={"separated": True})

    r = client.get("/api/contacts?separated=true")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Bob"

def test_list_contacts_include_separated(client, env):
    """``?include_separated=true`` keeps the soft-deleted
    row visible (but doesn't filter to it). Total = 3."""
    pid = env["bob"].id
    client.patch(f"/api/contacts/{pid}", json={"separated": True})

    r = client.get("/api/contacts?include_separated=true")
    assert r.status_code == 200
    assert r.json()["total"] == 3

def test_list_contacts_with_notes_returns_only_noted(env, client):
    """``?with_notes=true`` returns contacts that have at
    least one row in the ``contact_notes`` table (the
    LLM-recorded directory). Charlie stays empty."""
    from magi.db import ContactNote, open_session

    with open_session() as db:
        db.add(ContactNote(
            contact_id=env["bob"].id,
            note="Met at Q1 review.",
        ))
        db.commit()

    r = client.get("/api/contacts?with_notes=true")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Bob"

def test_get_contact_returns_full_row(client, env):
    """Single-row GET returns the full contact shape."""
    pid = env["bob"].id
    r = client.get(f"/api/contacts/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pid
    assert body["name"] == "Bob"
    assert body["display_name"] is None
    assert body["role"] == "assigned"
    assert body["telegram_id"] == 9002

def test_get_contact_404_for_missing_id(client):
    r = client.get("/api/contacts/9999")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found.contact"

def test_create_contact_minimal(client):
    """POST with just ``name`` + ``role`` default 'guest'."""
    r = client.post(
        "/api/contacts",
        json={"name": "Dana", "role": "guest"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Dana"
    assert body["role"] == "guest"

def test_create_contact_full(client):
    """POST with display_name / telegram_id round-trips."""
    r = client.post(
        "/api/contacts",
        json={
            "name": "Eve",
            "display_name": "E",
            "role": "assigned",
            "telegram_id": 9004,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["display_name"] == "E"
    assert body["telegram_id"] == 9004

def test_create_contact_requires_name(client):
    r = client.post("/api/contacts", json={"role": "guest"})
    assert r.status_code == 422  # Pydantic validation (min_length=1)

def test_create_contact_blank_name_rejected(client):
    """An all-whitespace name is rejected by the strip + empty check."""
    r = client.post("/api/contacts", json={"name": "   ", "role": "guest"})
    assert r.status_code == 400
    assert r.json()["code"] == "validation.name_required"

def test_create_contact_duplicate_name(client, env):
    """Two rows with the same name -> 409."""
    pid = env["alice"].id
    # Charlie already exists.
    r = client.post(
        "/api/contacts",
        json={"name": "Charlie", "role": "guest"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "conflict.contact_name_exists"

def test_create_contact_invalid_role(client):
    r = client.post(
        "/api/contacts",
        json={"name": "X", "role": "minion"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.role_unknown"

def test_create_contact_duplicate_telegram_id(client, env):
    """Bob has telegram_id=9002; trying to bind 9002 to a
    new row returns 409."""
    r = client.post(
        "/api/contacts",
        json={"name": "NewB", "role": "guest", "telegram_id": 9002},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "conflict.telegram_id_already_bound"

def test_patch_contact_renames(client, env):
    """``PATCH name`` updates + survives a re-GET."""
    pid = env["alice"].id
    r = client.patch(f"/api/contacts/{pid}", json={"name": "AliceNew"})
    assert r.status_code == 200
    assert r.json()["name"] == "AliceNew"
    r2 = client.get(f"/api/contacts/{pid}")
    assert r2.json()["name"] == "AliceNew"

def test_patch_contact_soft_deletes(client, env):
    """``separated=true`` stamps ``separated_at``;
    ``separated=false`` clears it back."""
    pid = env["bob"].id
    r = client.patch(f"/api/contacts/{pid}", json={"separated": True})
    assert r.status_code == 200
    assert r.json()["separated_at"] is not None

    r = client.patch(f"/api/contacts/{pid}", json={"separated": False})
    assert r.status_code == 200
    assert r.json()["separated_at"] is None

def test_patch_contact_changes_role(client, env):
    """``role`` update passes the enum check."""
    pid = env["charlie"].id
    r = client.patch(f"/api/contacts/{pid}", json={"role": "assigned"})
    assert r.status_code == 200
    assert r.json()["role"] == "assigned"

def test_patch_contact_invalid_role_400(client, env):
    """Bad role enum -> 400 (not silent pass-through)."""
    pid = env["alice"].id
    r = client.patch(f"/api/contacts/{pid}", json={"role": "ceo"})
    assert r.status_code == 400
    assert r.json()["code"] == "validation.role_unknown"

def test_patch_contact_unbinds_telegram_id(client, env):
    """``telegram_id=null`` clears the binding; ``null`` is
    explicitly distinguished from "don't change" via
    ``model_fields_set``."""
    pid = env["bob"].id
    r = client.patch(f"/api/contacts/{pid}", json={"telegram_id": None})
    assert r.status_code == 200
    assert r.json()["telegram_id"] is None

def test_patch_contact_dup_telegram_id_409(client, env):
    """Trying to assign a TG id that's bound to a *different*
    contact returns 409, not silent overwrite."""
    pid = env["alice"].id
    r = client.patch(f"/api/contacts/{pid}", json={"telegram_id": 9002})
    assert r.status_code == 409
    assert r.json()["code"] == "conflict.telegram_id_already_bound"

def test_get_contacts_requires_admin(charlie_client):
    """No admin cookie -> 401."""
    r = charlie_client.get("/api/contacts")
    assert r.status_code == 401

def test_get_single_contact_requires_admin(charlie_client, env):
    """Single-row read also gated by ``admin_gate``."""
    pid = env["alice"].id
    r = charlie_client.get(f"/api/contacts/{pid}")
    assert r.status_code == 401

def test_post_contacts_requires_admin(charlie_client):
    """POST refused at the gate."""
    r = charlie_client.post("/api/contacts", json={"name": "X", "role": "guest"})
    assert r.status_code == 401

def test_patch_contacts_requires_admin(charlie_client, env):
    """PATCH refused at the gate."""
    pid = env["alice"].id
    r = charlie_client.patch(f"/api/contacts/{pid}", json={"name": "X"})
    assert r.status_code == 401

def test_pagination_metadata(client, env):
    """The page envelope computes total_pages correctly."""
    # Alice + Bob + Charlie = 3. page_size=2 -> total_pages=2.
    r = client.get("/api/contacts?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2

    # page 2: 1 remaining.
    r = client.get("/api/contacts?page=2&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["page"] == 2
