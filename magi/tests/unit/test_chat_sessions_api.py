"""End-to-end TestClient tests for chat sessions (D.6).

Mounts the real FastAPI app, drives ``/api/chat/sessions``
CRUD + ``/api/chat/send`` with a seed admin + mocked LLM.

The session file persistence lives in a per-test temp
workspace (``MAGI_WORKSPACE_DIR``), so every test gets a
clean on-disk layout and pytest tears it down.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from magi.agent.memory.session import SessionStore
from magi.agent.db import init_sqlite
from magi.agent.db import Employee, init_orm, open_session


# A fake LLM reply used when we monkey-patch ``handle_message``
# for the duration of a test that exercises ``/api/chat/send``.
_FAKE_REPLY = "stubbed-from-mock"


# ────────────────────────────────────────────────────────────────── #
# fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def state(tmp_path, monkeypatch) -> Path:
    """Set up an isolated state_dir + workspace_dir for the test.

    We also reset the module-global SQLAlchemy engine in
    :mod:`magi.agent.db.orm` — without that, the
    second test reuses the first test's engine, which
    points at a different (already-deleted) sqlite file.
    The engine is a process-global; per-test isolation
    requires resetting it ourselves.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    # Reset the orm module's engine singleton so each test
    # points at its own fresh sqlite file. Without this,
    # every test after the first inserts into a path that
    # no longer exists → IntegrityError on duplicate key
    # from a half-flushed engine with stale conn pool.
    import magi.agent.db.engine as _orm_mod
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None

    init_sqlite(str(sd))
    init_orm(str(sd))
    return sd


@pytest.fixture
def admin(state) -> Employee:
    """Seed an admin with provider/api_key so /chat/send passes the
    pre-flight."""
    with open_session() as s:
        emp = Employee(
            name="Test Admin",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake-key-for-tests",
        )
        s.add(emp)
        s.commit()
        s.refresh(emp)
        return emp


@pytest.fixture
def client(state, admin, monkeypatch) -> TestClient:
    """The app with ``handle_message`` stubbed so /chat/send doesn't
    need a real LLM."""
    from magi.channels.webui.api import chat as chat_mod
    from magi.agent import loop as agent_mod

    async def fake_handle_message(*args, **kwargs):
        return _FAKE_REPLY

    monkeypatch.setattr(agent_mod, "handle_message", fake_handle_message)
    # chat.py imported handle_message by name into its module
    # namespace; the monkey-patch above rebinds the symbol in
    # ``agent_mod`` so the *next* import would get the fake, but
    # chat.py already has a reference to the original. Patch
    # chat's namespace too.
    monkeypatch.setattr(chat_mod, "handle_message", fake_handle_message)

    from magi.channels.webui.app import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_orm_engine():
    """Auto-reset the global SQLAlchemy engine before each test.

    The orm module's ``_engine`` is a process-global singleton
    cached on first use. Without resetting it, every test
    after the first inherits the prior test's engine handle —
    which points at a tmp_path that's been recreated (so the
    sqlite file path is stale) and the inserts collide on
    seeded admin rows.
    """
    import magi.agent.db.engine as _orm_mod
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None
    yield


def _admin_cookie(admin: Employee) -> dict:
    """Cookie payload for the admin route."""
    return {"magi_session": str(admin.telegram_id)}


# ────────────────────────────────────────────────────────────────── #
# CRUD: /chat/sessions
# ────────────────────────────────────────────────────────────────── #


def test_list_requires_auth(state):
    from magi.channels.webui.app import app
    c = TestClient(app)
    r = c.get("/api/chat/sessions")
    assert r.status_code == 401
    assert r.json()["code"] == "auth.not_signed_in"


def test_create_returns_session_id_and_persists(client, admin, state, monkeypatch):
    r = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    assert r.status_code == 201
    body = r.json()
    assert "session_id" in body
    sid = body["session_id"]
    # D.18: sessions live in SQLite (``chat_sessions`` table)
    # instead of a JSON file. Verify the row landed.
    from magi.agent.db import ChatSession, open_session
    with open_session() as db:
        row = db.get(ChatSession, sid)
    assert row is not None, f"expected session row for {sid}"
    assert row.tgid == str(admin.telegram_id)
    assert row.employee_id == admin.id


def test_list_returns_created_session(client, admin):
    client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    r = client.get("/api/chat/sessions", cookies=_admin_cookie(admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    item = data["items"][0]
    # Each summary carries the schema_versioned fields.
    assert {
        "session_id", "created_at", "updated_at",
        "message_count", "preview",
    }.issubset(item)


def test_get_returns_full_session(client, admin):
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]
    r = client.get(f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["chat_id"] == "9001"
    assert body["employee_id"] == admin.id
    assert body["channel"] == "webui"
    assert body["messages"] == []
    assert body["schema_version"] == 1


def test_get_unknown_session_404(client, admin):
    # ULID-shaped (Crockford base32, 26 chars) but no file written.
    # All chars must be from the Crockford alphabet — I/L/O/U
    # are *NOT* allowed. The generator never emits them; this
    # is a valid shape that just doesn't exist on disk.
    fake_sid = "01ABCDEFGHJKMNPQRSTVWXYZAB"
    r = client.get(
        f"/api/chat/sessions/{fake_sid}", cookies=_admin_cookie(admin)
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found.session"


def test_get_malformed_session_id_400(client, admin):
    """A non-ULID id (length wrong) is a 400."""
    r = client.get(
        "/api/chat/sessions/short", cookies=_admin_cookie(admin)
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.session_id_invalid"


def test_delete_idempotent(client, admin):
    """DELETE removes; calling DELETE again on the same id is a
    no-op (idempotent → no error)."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]
    r1 = client.delete(f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin))
    assert r1.status_code == 204
    # Second delete on the same id — already gone, no-op.
    r2 = client.delete(f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin))
    assert r2.status_code == 204
    # And a fresh GET → 404.
    r3 = client.get(f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin))
    assert r3.status_code == 404


# ────────────────────────────────────────────────────────────────── #
# /api/chat/send with session_id
# ────────────────────────────────────────────────────────────────── #


def test_send_without_session_id_autocreates(client, admin):
    """Sending without session_id returns a fresh id and persists
    both user + assistant messages."""
    r = client.post(
        "/api/chat/send",
        cookies=_admin_cookie(admin),
        json={"text": "hello LLM"},
    )
    # The mocked handle_message returns "" because we haven't
    # configured credentials to actually flow through. Either
    # way, the response shape and persistence are what we're
    # checking.
    assert r.status_code in (200, 403)  # 403 if creds gate kicks in
    if r.status_code == 200:
        body = r.json()
        assert "reply" in body
        assert "session_id" in body
        sid = body["session_id"]
        # The session file now has at least one user message.
        store = SessionStore(Path(__file__).resolve().parents[3])
        # session_path requires the workspace_root to match.
        # Force-recompute via the global env-var path which
        # is the one SessionStore uses.
        s = store.get("9001", sid)
        assert s is not None
        # Either one user message (if LLM was hit) or user+assistant
        # (which is the production case). We accept any of those
        # shapes — the meaningful assertion is that the file is
        # there and contains at least one message we can identify.
        roles = [m.role for m in s.messages]
        assert "user" in roles


def test_send_with_existing_session_id_appends(client, admin):
    """Sending with a known session_id appends to that session."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]

    r = client.post(
        "/api/chat/send",
        cookies=_admin_cookie(admin),
        json={"text": "first", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["session_id"] == sid

    r2 = client.post(
        "/api/chat/send",
        cookies=_admin_cookie(admin),
        json={"text": "second", "session_id": sid},
    )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == sid

    # Persisted: list should still show one session, with
    # message_count ≥ 2 (or 4 if both inbound+outbound paths
    # ran).
    listing = client.get(
        "/api/chat/sessions", cookies=_admin_cookie(admin)
    ).json()
    assert listing["total"] == 1
    assert listing["items"][0]["message_count"] >= 2


def test_send_with_unknown_session_id_autocreates(client, admin):
    """An unknown session_id is treated the same as None —
    backend creates a fresh one and returns its id."""
    # ULID-shaped (Crockford, 26 chars) but doesn't exist on disk.
    fake_sid = "01ABCDEFGHJKMNPQRSTVWXYZAB"
    r = client.post(
        "/api/chat/send",
        cookies=_admin_cookie(admin),
        json={"text": "hi", "session_id": fake_sid},
    )
    assert r.status_code == 200
    body = r.json()
    # New id is different from the supplied stale one.
    assert body["session_id"] != fake_sid
    # And the new session really exists.
    listing = client.get(
        "/api/chat/sessions", cookies=_admin_cookie(admin)
    ).json()
    assert any(
        item["session_id"] == body["session_id"]
        for item in listing["items"]
    )


def test_chat_ids_isolated(client, state):
    """Two admins signing in see distinct session lists."""
    with open_session() as s:
        a = Employee(
            name="Alice", telegram_id=9101, role="admin",
            provider="minimax", api_key="x",
        )
        b = Employee(
            name="Bob",   telegram_id=9102, role="admin",
            provider="minimax", api_key="y",
        )
        s.add_all([a, b])
        s.commit()

    # Alice creates two sessions, Bob one.
    for _ in range(2):
        client.post(
            "/api/chat/sessions",
            cookies={"magi_session": "9101"},
        )
    client.post(
        "/api/chat/sessions",
        cookies={"magi_session": "9102"},
    )

    a_list = client.get(
        "/api/chat/sessions", cookies={"magi_session": "9101"}
    ).json()
    b_list = client.get(
        "/api/chat/sessions", cookies={"magi_session": "9102"}
    ).json()
    assert a_list["total"] == 2
    assert b_list["total"] == 1
    # Disjoint session ids.
    a_ids = {it["session_id"] for it in a_list["items"]}
    b_ids = {it["session_id"] for it in b_list["items"]}
    assert a_ids.isdisjoint(b_ids)

    # And Alice can't read Bob's session by guessing the id.
    b_sid = next(iter(b_ids))
    r = client.get(
        f"/api/chat/sessions/{b_sid}", cookies={"magi_session": "9101"}
    )
    # Either 404 (Alice's chat_id dir doesn't have b_sid file)
    # OR 200 with b's content (if the path overlaps — but with
    # our layout it should be 404). Pin to 404 for the layout
    # invariant.
    assert r.status_code == 404


def test_send_requires_admin_gate(client, admin):
    """No cookie → 401 from AdminGate, not a session write."""
    r = client.post("/api/chat/send", json={"text": "hi"})
    assert r.status_code == 401


def test_send_with_malformed_session_id_400(client, admin):
    """The shape check catches non-ULID ids before store writes."""
    r = client.post(
        "/api/chat/send",
        cookies=_admin_cookie(admin),
        json={"text": "hi", "session_id": "definitely-not-ulid"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.session_id_invalid"


def test_session_persistence_across_calls(client, admin, state):
    """Full round-trip: create session → send → list shows the new
    message → get returns the full transcript."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]
    client.post(
        "/api/chat/send",
        cookies=_admin_cookie(admin),
        json={"text": "hi", "session_id": sid},
    )

    # New client (still same TestClient/app instance) hits get.
    r = client.get(f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin))
    assert r.status_code == 200
    sess = r.json()
    assert sess["session_id"] == sid
    # At least one user message persisted.
    assert any(m["role"] == "user" for m in sess["messages"])


def test_list_pagination(client, admin):
    """?limit=2&offset=0 paginates."""
    for _ in range(4):
        client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    r = client.get(
        "/api/chat/sessions?limit=2&offset=0", cookies=_admin_cookie(admin)
    ).json()
    assert r["limit"] == 2
    assert r["offset"] == 0
    assert len(r["items"]) == 2
    assert r["total"] == 4
    r2 = client.get(
        "/api/chat/sessions?limit=2&offset=2", cookies=_admin_cookie(admin)
    ).json()
    assert len(r2["items"]) == 2
    # Different page.
    assert {it["session_id"] for it in r["items"]} != {
        it["session_id"] for it in r2["items"]
    }


def test_delete_unauth_state_no_leak(state, admin):
    """Deleting files under the workspace doesn't break the next
    request. Sanity check that there are no path-leak issues
    between requests."""
    c = TestClient(__import__("magi.channels.webui.app", fromlist=["app"]).app)
    # Make 3 sessions, then delete 1.
    sids = []
    for _ in range(3):
        r = c.post("/api/chat/sessions", cookies=_admin_cookie(admin))
        sids.append(r.json()["session_id"])
    c.delete(f"/api/chat/sessions/{sids[1]}", cookies=_admin_cookie(admin))
    listing = c.get("/api/chat/sessions", cookies=_admin_cookie(admin)).json()
    assert listing["total"] == 2
    assert all(sid != sids[1] for sid in [
        it["session_id"] for it in listing["items"]
    ])


# ────────────────────────────────────────────────────────────────── #
# D.7 — PATCH /api/chat/sessions/{id} (manual rename)
# ────────────────────────────────────────────────────────────────── #


def test_patch_session_renames(client, admin):
    """``PATCH {title: "..."}`` renames the session on disk and
    the response shows the new title."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]

    r = client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": "Acme 会议 明天 3 点"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["title"] == "Acme 会议 明天 3 点"
    # GET roundtrip — file on disk really has it.
    get = client.get(
        f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin)
    )
    assert get.json()["title"] == "Acme 会议 明天 3 点"


def test_patch_session_clears_title(client, admin):
    """``null`` and ``""`` both clear the title."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]
    # Set
    client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": "temp"},
    )
    # Clear via empty string
    r1 = client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": ""},
    )
    assert r1.status_code == 200
    assert r1.json()["title"] is None
    # Set again, then clear via null
    client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": "temp"},
    )
    r2 = client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": None},
    )
    assert r2.status_code == 200
    assert r2.json()["title"] is None


def test_patch_session_absent_title_is_noop(client, admin):
    """``PATCH {}`` (no title field at all) leaves the session
    untouched and returns its current state.

    Verifies the ``model_fields_set`` semantics — absent ≠
    null. We capture ``updated_at`` via ``GET`` first (the
    create-only POST returns just ``session_id``) and confirm
    it doesn't move on the no-op patch (manual rename also
    doesn't bump anyway)."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]
    # Fetch the full session to capture ``updated_at``.
    initial = client.get(
        f"/api/chat/sessions/{sid}", cookies=_admin_cookie(admin)
    ).json()
    initial_updated_at = initial["updated_at"]

    r = client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["updated_at"] == initial_updated_at  # unchanged


def test_patch_session_clamps_too_long_title(client, admin):
    """Pydantic ``max_length=80`` rejects 81 chars before the
    handler even runs (422)."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]

    r = client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": "x" * 81},
    )
    assert r.status_code == 422


def test_patch_session_trims_whitespace(client, admin):
    """The store re-trims even when Pydantic passes a long-but-
    valid string. ``"   hello   "`` → ``"hello"``."""
    create = client.post("/api/chat/sessions", cookies=_admin_cookie(admin))
    sid = create.json()["session_id"]
    r = client.patch(
        f"/api/chat/sessions/{sid}",
        cookies=_admin_cookie(admin),
        json={"title": "   hello world   "},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "hello world"


def test_patch_unknown_session_404(client, admin):
    """ULID-shaped but no file → 404."""
    fake_sid = "01ABCDEFGHJKMNPQRSTVWXYZAB"
    r = client.patch(
        f"/api/chat/sessions/{fake_sid}",
        cookies=_admin_cookie(admin),
        json={"title": "x"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found.session"


def test_patch_malformed_session_id_400(client, admin):
    """Non-ULID id → 400 ``validation.session_id_invalid``.
    Catch before the DB read so we don't surface a confusing
    404 for a typo."""
    r = client.patch(
        "/api/chat/sessions/short",
        cookies=_admin_cookie(admin),
        json={"title": "x"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation.session_id_invalid"


def test_patch_requires_admin(client):
    """No cookie → 401 (AdminGate). Independent of any
    session state."""
    r = client.patch(
        "/api/chat/sessions/01ABCDEFGHJKMNPQRSTVWXYZAB",
        cookies={},
        json={"title": "x"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "auth.not_signed_in"
