"""Tests for the D.18+2 session-messages pagination.

Three surfaces pinned:

  1. **SessionStore.get_messages_page** — the underlying
     storage primitive. Returns ``(messages, total_active,
     total_all)`` for the requested tail slice. Sorts
     chronologically within the page; the offset counts
     *from the newest end* so ``offset=limit`` gives the
     next older page.
  2. **GET /api/chat/sessions/{id}/messages** — the HTTP
     route. Wraps the storage call in tgid scope +
     404 handling. Pins the response shape so the WebUI
     consumer doesn't break on a future schema bump.
  3. **Edge cases** — empty sessions, unknown session_id
     (404), malformed session_id (400), pagination past
     the end (empty ``messages`` but ``total_active``
     still accurate).
"""

from __future__ import annotations

import pytest


# ────────────────────────────────────────────────────────────────── #
# shared fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def admin_env(monkeypatch, tmp_path):
    """Per-test isolated state dir + ORM engine.

    Mirrors the fixtures in test_sessions.py /
    test_chat_sessions_api.py so the tests below can rely
    on a clean slate each time.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import init_sqlite
    from magi.agent.db import Employee, init_orm, open_session
    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as db:
        db.add(Employee(
            name="TA-pagination", telegram_id=9001,
            role="admin", provider="minimax", api_key="fake",
        ))
        db.add(Employee(
            name="TB-other", telegram_id=9002,
            role="admin", provider="minimax", api_key="fake",
        ))
        db.commit()

    return state


def _seed_messages(store, tgid: str, count: int) -> str:
    """Append ``count`` synthetic user messages to a fresh
    session for ``tgid`` and return the session_id.
    The synthetic text is just ``"msg-{idx}"`` — enough
    to be distinct without being noisy.
    """
    from magi.agent.memory.session import SessionMessage, new_session_id

    # D.23: store key is uid (int); tgid is the
    # per-channel delivery address stamped on the row.
    sess = store.create(1, )
    msgs = [
        SessionMessage(
            role="user", text=f"msg-{i}",
            ts="2026-07-03T00:00:00Z",
            message_id=new_session_id(),
        )
        for i in range(count)
    ]
    store.append_messages(1, sess.session_id, msgs)
    return sess.session_id


# ────────────────────────────────────────────────────────────────── #
# SessionStore.get_messages_page — storage layer
# ────────────────────────────────────────────────────────────────── #


def test_get_messages_page_returns_tail_slice(admin_env):
    """Newest ``limit`` active messages in chronological
    order. ``offset=0`` is the latest page."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 5)

    msgs, total_active, total_all = store.get_messages_page(
        1, sid, limit=2, offset=0,
    )
    assert total_active == 5
    assert total_all == 5
    assert len(msgs) == 2
    # The 2 newest are msg-3 and msg-4 (chronological
    # within the page — the page is sorted ASC).
    assert [m.text for m in msgs] == ["msg-3", "msg-4"]


def test_get_messages_page_offset_skips_newest(admin_env):
    """``offset=limit`` returns the next older page — msg-1
    and msg-2, in that order."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 5)

    msgs, total_active, _ = store.get_messages_page(
        1, sid, limit=2, offset=2,
    )
    assert total_active == 5
    assert [m.text for m in msgs] == ["msg-1", "msg-2"]


def test_get_messages_page_offset_zero_size_limit(admin_env):
    """``limit=1, offset=0`` then ``offset=1`` covers all 5
    messages with no gaps and no duplicates. Offset
    counts from the newest end, so page 0 is msg-4,
    page 1 is msg-3, etc."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 5)

    seen = []
    for off in range(5):
        msgs, _, _ = store.get_messages_page(
            1, sid, limit=1, offset=off,
        )
        assert len(msgs) == 1, f"offset {off} should have 1 row"
        seen.append(msgs[0].text)
    assert seen == ["msg-4", "msg-3", "msg-2", "msg-1", "msg-0"]


def test_get_messages_page_past_end_returns_empty(admin_env):
    """Asking past the end returns ``[]`` but the totals
    still reflect the full count — the UI uses this to
    hide the load-more button."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 3)

    msgs, total_active, total_all = store.get_messages_page(
        1, sid, limit=10, offset=99,
    )
    assert msgs == []
    assert total_active == 3
    assert total_all == 3


def test_get_messages_page_excludes_archive_by_default(admin_env):
    """Archive rows (``archived=1``) don't appear in the
    default page. ``total_active`` and ``total_all``
    differ — the UI uses this to decide whether to show
    a separate "show archive" affordance later."""
    from magi.agent.memory.session import SessionStore, SessionMessage, new_session_id
    from magi.agent.db import ChatMessage, open_session

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 3)

    # Manually insert 2 archive rows.
    with open_session() as db:
        for i in range(2):
            db.add(ChatMessage(
                session_id=sid,
                message_id=new_session_id(),
                role="user", text=f"archive-{i}",
                ts="2026-07-01T00:00:00Z", archived=1,
            ))
        db.commit()

    msgs, total_active, total_all = store.get_messages_page(
        1, sid, limit=10, offset=0,
    )
    assert len(msgs) == 3  # active only
    assert total_active == 3
    assert total_all == 5
    assert all("archive" not in m.text for m in msgs)


def test_get_messages_page_include_archived_appends_archive(admin_env):
    """With ``include_archived=True``, archive rows are
    appended after the active page (also in chronological
    order — they sort by ``id`` ASC, which is the
    insertion order)."""
    from magi.agent.memory.session import SessionStore, new_session_id
    from magi.agent.db import ChatMessage, open_session

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 2)
    with open_session() as db:
        db.add(ChatMessage(
            session_id=sid,
            message_id=new_session_id(),
            role="user", text="archive-0",
            ts="2026-07-01T00:00:00Z", archived=1,
        ))
        db.commit()

    msgs, total_active, total_all = store.get_messages_page(
        1, sid, limit=10, offset=0, include_archived=True,
    )
    # 2 active + 1 archive = 3 total.
    assert total_active == 2
    assert total_all == 3
    assert len(msgs) == 3
    # Active rows first (chronological), then archive.
    assert msgs[0].text == "msg-0"
    assert msgs[1].text == "msg-1"
    assert msgs[2].text == "archive-0"


def test_get_messages_page_unknown_session_returns_zeros(admin_env):
    """An unknown session_id returns zeros and an empty
    page. The HTTP layer is responsible for translating
    ``([], 0, 0)`` into a 404 when offset==0."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    msgs, total_active, total_all = store.get_messages_page(
        "9001", "01ABCDEFGHJKMNPQRSTVWXYZAB", limit=10, offset=0,
    )
    assert msgs == []
    assert total_active == 0
    assert total_all == 0


def test_get_messages_page_respects_employee_id_scope(admin_env):
    """A session belonging to employee 2 is invisible when
    queried via employee 1 — same WHERE-clause enforcement
    as the rest of the API.

    D.23: the store key is now ``uid``; the
    pre-D.23 tgid scope test is rewritten here in
    terms of the new key, since the tgid column no
    longer drives the WHERE clause.
    """
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9002", 3)
    # _seed_messages uses uid=1 (TA-pagination);
    # the second admin row (TB-other) gets uid=2
    # — the auto-increment order of the fixture's inserts.
    # Querying with uid=2 (TB-other) for the
    # session that belongs to uid=1 must return
    # nothing.

    msgs, total_active, _ = store.get_messages_page(
        2, sid, limit=10, offset=0,
    )
    assert msgs == []
    assert total_active == 0


# ────────────────────────────────────────────────────────────────── #
# GET /api/chat/sessions/{id}/messages — HTTP route
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def client(admin_env):
    """TestClient with admin-A's cookie (tgid 9001)."""
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "1")
    return c


def test_messages_route_default_page(client, admin_env):
    """``GET /api/chat/sessions/{id}/messages`` (no params)
    returns up to 50 active messages + totals."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 5)

    r = client.get(f"/api/chat/sessions/{sid}/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["total_active"] == 5
    assert body["total_all"] == 5
    assert body["offset"] == 0
    assert body["limit"] == 50  # the default
    assert len(body["messages"]) == 5


def test_messages_route_pagination_via_offset(client, admin_env):
    """Two pages with ``limit=2, offset=0`` and
    ``offset=2`` cover a 5-message session without gaps
    or duplicates."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 5)

    p0 = client.get(
        f"/api/chat/sessions/{sid}/messages?limit=2&offset=0"
    ).json()
    p1 = client.get(
        f"/api/chat/sessions/{sid}/messages?limit=2&offset=2"
    ).json()
    p2 = client.get(
        f"/api/chat/sessions/{sid}/messages?limit=2&offset=4"
    ).json()

    assert len(p0["messages"]) == 2
    assert len(p1["messages"]) == 2
    assert len(p2["messages"]) == 1
    # Offset 0/2 = newest two pages; offset 4 = oldest
    # remaining. Within each page the rows are
    # chronological (oldest first).
    seen = [m["text"] for m in p0["messages"] + p1["messages"] + p2["messages"]]
    assert seen == ["msg-3", "msg-4", "msg-1", "msg-2", "msg-0"]


def test_messages_route_404_for_unknown_session(client):
    r = client.get("/api/chat/sessions/01ABCDEFGHJKMNPQRSTVWXYZAB/messages")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found.session"


def test_messages_route_400_for_malformed_session_id(client):
    r = client.get("/api/chat/sessions/short/messages")
    assert r.status_code == 400
    assert r.json()["code"] == "validation.session_id_invalid"


def test_messages_route_401_without_cookie(admin_env):
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    c = TestClient(create_app())
    r = c.get("/api/chat/sessions/01ABCDEFGHJKMNPQRSTVWXYZAB/messages")
    assert r.status_code == 401


def test_messages_route_cross_employee_isolation(client, admin_env):
    """Alice (employee 1) cannot read Bob's (employee 2)
    session via pagination. The route scopes the WHERE
    clause by ``uid`` (D.23); an attacker who
    knows the session_id still gets a 404.
    """
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    # Bob's session: uid=2,  (the
    # tgid is the per-channel delivery address —
    # historical / metadata only now).
    sess = store.create(2, channel="webui")
    sid = sess.session_id

    r = client.get(f"/api/chat/sessions/{sid}/messages")
    assert r.status_code == 404


def test_messages_route_past_end_returns_empty_with_totals(client, admin_env):
    """``offset`` past the end returns ``messages: []`` but
    the totals still reflect the full session size — the
    UI hides the load-more button on this signal."""
    from magi.agent.memory.session import SessionStore

    store = SessionStore(str(admin_env))
    sid = _seed_messages(store, "9001", 2)

    r = client.get(f"/api/chat/sessions/{sid}/messages?limit=10&offset=99")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == []
    assert body["total_active"] == 2
    assert body["total_all"] == 2