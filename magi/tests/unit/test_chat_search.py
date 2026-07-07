"""End-to-end tests for the ``/api/chat/search`` endpoint (D.18).

Three surfaces pinned:

  1. **Hit + scoping** — a trigram-substring query that
     matches a message returns a hit, with chat_id scope
     enforced at the SQL join (a second admin's rows are
     invisible).
  2. **CJK friendly** — a 3-char Chinese substring query
     hits messages containing that run.
  3. **Query sanitisation** — FTS5 special chars in the
     query are wrapped into phrase syntax; the route
     returns 200 instead of 500.

Plus the auth gate (401 without the admin cookie) and the
chat_id cookie binding (the same cookie can't see another
chat's rows).
"""

from __future__ import annotations

import pytest


# ────────────────────────────────────────────────────────────────── #
# fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def search_env(monkeypatch, tmp_path):
    """Per-test isolated state dir + seeded two admins.

    Admin A (telegram_id=9001) and admin B (telegram_id=9002)
    each get a single session with one indexed message, so
    cross-chat scoping has something to bite on.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.agent.state.orm as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.state import init_sqlite
    from magi.agent.state.orm import Employee, init_orm, open_session
    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as s:
        s.add(Employee(
            name="A", telegram_id=9001, role="admin",
            provider="minimax", api_key="fake",
        ))
        s.add(Employee(
            name="B", telegram_id=9002, role="admin",
            provider="minimax", api_key="fake",
        ))
        s.commit()

    return state


def _seed_chat_message(chat_id: str, text: str) -> str:
    """Unused; tests use the ``seed_messages`` fixture below.
    Kept as a placeholder so a future caller can hit it
    without a fixture (would need to be plumbed via the
    env fixture's tmp_path explicitly).
    """
    raise NotImplementedError(
        "use the seed_messages fixture instead — it carries the "
        "MAGI_STATE_DIR / engine reset plumbing"
    )
@pytest.fixture
def seed_messages(search_env):
    from magi.agent.sessions import SessionStore, new_session_id
    from magi.agent.state.orm import ChatMessage, open_session

    counter = {"n": 0}

    def _seed(chat_id: str, text: str, *, employee_id: int = 1) -> str:
        # Each seeded message gets a fresh message_id so the
        # (session_id, message_id) UNIQUE constraint doesn't
        # reject the second seed in the same session.
        counter["n"] += 1
        msg_id = f"m{chat_id}-{counter['n']:04d}"
        store = SessionStore(str(search_env))
        sess = store.create(chat_id, employee_id=employee_id)
        with open_session() as db:
            db.add(ChatMessage(
                session_id=sess.session_id,
                message_id=msg_id,
                role="user",
                text=text,
                ts="2026-07-03T00:00:00Z",
                archived=0,
            ))
            db.commit()
        return sess.session_id

    return _seed


@pytest.fixture
def client(search_env):
    """TestClient with admin-A's cookie by default. Tests
    that want admin B's cookie just ``cookies.set()`` on
    the client after creation (TestClient supports it)."""
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "9001")
    return c


# ────────────────────────────────────────────────────────────────── #
# auth gate
# ────────────────────────────────────────────────────────────────── #


def test_search_requires_admin(search_env):
    """No cookie → 401 (AdminGate), no DB hit."""
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    c = TestClient(create_app())
    r = c.get("/api/chat/search?q=anything")
    assert r.status_code == 401
    assert r.json()["code"] == "auth.not_signed_in"


# ────────────────────────────────────────────────────────────────── #
# empty / trivial queries
# ────────────────────────────────────────────────────────────────── #


def test_search_empty_query_returns_empty(client):
    """``?q=`` short-circuits to ``[]`` without touching the DB."""
    r = client.get("/api/chat/search?q=")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["q"] == ""


def test_search_whitespace_query_returns_empty(client):
    r = client.get("/api/chat/search?q=%20%20%20")
    assert r.status_code == 200
    assert r.json()["items"] == []


# ────────────────────────────────────────────────────────────────── #
# happy path + CJK
# ────────────────────────────────────────────────────────────────── #


def test_search_english_substring_match(client, seed_messages):
    """A 3-char substring match returns the right hit."""
    seed_messages("9001", "hello world foo bar baz quux")
    r = client.get("/api/chat/search?q=foo")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(
        "foo" in h["snippet"].lower()
        for h in body["items"]
    )


def test_search_chinese_3char_match(client, seed_messages):
    """CJK substring: a 3-character Chinese run matches the
    trigram-tokenized index (unicode61 would tokenize the
    whole Han string as one token — only full-substring
    from a boundary would hit)."""
    seed_messages("9001", "压缩触发器阈值默认百分之八十")
    # Search for an interior 3-char run.
    r = client.get("/api/chat/search?q=压缩触发")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(
        "压缩" in h["snippet"]
        for h in body["items"]
    )


def test_search_too_short_chinese_returns_zero(client, seed_messages):
    """Below the trigram minimum (3 chars) the query has no
    matches — the FTS5 index can't help with shorter runs.
    Operators get an empty result, not an error."""
    seed_messages("9001", "压缩触发器阈值")
    r = client.get("/api/chat/search?q=压缩")
    assert r.status_code == 200
    assert r.json()["total"] == 0


# ────────────────────────────────────────────────────────────────── #
# chat_id scoping
# ────────────────────────────────────────────────────────────────── #


def test_search_scoped_to_caller_employee(client, search_env, seed_messages):
    """Admin A's search doesn't return admin B's messages,
    even though both are in the same SQLite DB.

    Scope is the calling Employee row (D.18+1 cross-platform
    scope: ``WHERE chat_sessions.employee_id = :emp``). We
    seed for admin A (employee_id=1) and admin B
    (employee_id=2) using the same ``tgid`` so the scope
    is what discriminates — not the chat identifier.
    """
    seed_messages("9001", "alpha unique-token-xyz alpha", employee_id=1)
    seed_messages("9001", "beta  unique-token-xyz beta",  employee_id=2)

    r = client.get("/api/chat/search?q=unique-token-xyz")
    assert r.status_code == 200
    body = r.json()
    # Only admin A's row appears.
    assert body["total"] == 1
    assert body["employee_id"] == 1


def test_search_scoped_when_admin_b_signs_in(search_env, seed_messages):
    """The same query, signed in as admin B (employee_id=2),
    returns admin B's row only (not admin A's)."""
    seed_messages("9001", "alpha shared-key-123 alpha", employee_id=1)
    seed_messages("9001", "beta  shared-key-123 beta",  employee_id=2)

    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    c = TestClient(create_app())
    c.cookies.set("magi_session", "9002")
    r = c.get("/api/chat/search?q=shared-key-123")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["employee_id"] == 2


# ────────────────────────────────────────────────────────────────── #
# query sanitisation
# ────────────────────────────────────────────────────────────────── #


def test_search_handles_fts_operators_in_query(client, seed_messages):
    """Raw ``"``, ``*``, ``AND``, etc. would normally break
    FTS5. The route wraps each token into a phrase so the
    query parses regardless of the operator chars."""
    seed_messages("9001", "this is a normal message")
    # Unbalanced quote + operator chars in the query.
    r = client.get('/api/chat/search?q="AND OR NOT hello')
    assert r.status_code == 200
    # Phrase wrapping means the operators are literal — no
    # match, but the request still returns a clean 200 (not
    # the 500 / 400 that an unbalanced FTS5 expression
    # would otherwise cause).
    body = r.json()
    # "hello" doesn't appear in our seed text, so we expect
    # zero hits — but no error.
    assert body["total"] == 0


def test_search_query_with_unbalanced_quote(client, seed_messages):
    """Same guarantee for a literal unbalanced ``"`` char."""
    seed_messages("9001", "she said hello then left")
    r = client.get('/api/chat/search?q=hello"')
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────── #
# result shape
# ────────────────────────────────────────────────────────────────── #


def test_search_response_shape(client, seed_messages):
    """Pinned: result row exposes the fields the frontend
    needs to deep-link into the matching thread."""
    seed_messages("9001", "compression triggers at 80 percent of context")
    r = client.get("/api/chat/search?q=compression")
    body = r.json()
    assert body["q"] == "compression"
    assert body["employee_id"] == 1
    assert body["limit"] == 20
    assert body["offset"] == 0
    item = body["items"][0]
    assert {"session_id", "message_id", "role", "ts",
            "snippet", "title", "score",
            "tgid", "channel"}.issubset(item)
    # The snippet wraps the matched substring in <mark>.
    assert "<mark>" in item["snippet"]


def test_search_pagination(client, seed_messages):
    """?limit=1&offset=0 returns the first hit; ?limit=1&offset=1
    the next one."""
    seed_messages("9001", "match-aaa-alpha")
    seed_messages("9001", "match-aaa-beta")
    seed_messages("9001", "match-aaa-gamma")
    # Three sessions, one message each.
    r1 = client.get("/api/chat/search?q=match-aaa&limit=1&offset=0").json()
    r2 = client.get("/api/chat/search?q=match-aaa&limit=1&offset=1").json()
    assert r1["total"] == 3
    assert r2["total"] == 3
    assert len(r1["items"]) == 1
    assert len(r2["items"]) == 1
    # Different page items.
    assert r1["items"][0]["message_id"] != r2["items"][0]["message_id"]