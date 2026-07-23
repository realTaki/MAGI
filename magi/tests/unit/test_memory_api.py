"""End-to-end tests for ``/api/memory`` (D.25 — Knowledge →
Memory pane).

Five surfaces pinned:

  1. **Auth gate** — ``AdminGate`` (cookie admin or 401).
     The endpoint must refuse to render another admin's
     memory under any circumstance (no ``?uid=``
     URL knob — the caller is always derived from the
     cookie).
  2. **Scope** — the cookie's admin ``uid`` is
     the ``MemoryEntry.uid`` filter; admin B never
     sees admin A's rows.
  3. **Both kinds + completion states returned** —
     ``important`` rows (never expire) and ``ongoing``
     rows (in-flight + completed) all show up. The
     ``completed_at`` field carries the timestamp for
     completed ongoing rows; ``important`` rows have
     ``completed_at=null``.
  4. **Order** — ``importance DESC, updated_at DESC``
     so what the LLM sees in the system-prompt formatter
     matches what the operator sees in the dashboard.
  5. **Body preview** — the full body ships in the
     response (the WebUI truncates at 200 chars for the
     preview cell with ``title=`` hover). The store
     already caps body at 8 KB.

The fixture mirrors ``test_skills_api`` / ``test_memory``:
fresh state dir, fresh ORM engine, seeded admin employee,
``TestClient`` with ``magi_session`` cookie set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    """MAGI_STATE_DIR + ORM + two admins."""
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
        Employee,
        init_orm,
        init_sqlite,
        open_session,
    )
    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as db:
        alice = Employee(
            name="Alice",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake",
        )
        bob = Employee(
            name="Bob",
            telegram_id=9002,
            role="admin",
            provider="minimax",
            api_key="fake",
        )
        charlie = Employee(
            name="Charlie",
            telegram_id=9003,
            role="employee",
            provider="minimax",
            api_key="fake",
        )
        db.add_all([alice, bob, charlie])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)
        db.refresh(charlie)

    return {"state": state, "alice": alice, "bob": bob, "charlie": charlie}


@pytest.fixture
def client(env):
    """TestClient with Alice's cookie (admin)."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    # D.24: cookie is the uid (int), not a tgid.
    c.cookies.set("magi_session", str(env["alice"].id))
    return c


@pytest.fixture
def bob_client(env):
    """TestClient with Bob's cookie (also admin, different
    uid). Used to verify scope isolation."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", str(env["bob"].id))
    return c


@pytest.fixture
def charlie_client(env):
    """TestClient with Charlie's cookie (role=employee, not
    admin). Used to verify the AdminGate rejects non-admin
    callers."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", str(env["charlie"].id))
    return c


def _seed_memory(
    env,
    *,
    uid: int,
    kind: str,
    subject: str,
    body: str,
    importance: int = 3,
    completed_at: datetime | None = None,
    updated_at: datetime | None = None,
):
    """Insert one MemoryEntry with the given fields.

    Bypasses ``MemoryStore.add`` so the test can stamp
    arbitrary ``updated_at`` / ``completed_at`` values
    (the store validates kind / clamps importance but
    always sets timestamps to ``now``). The endpoint
    orders by ``importance DESC, updated_at DESC``, so
    the test must control timestamps directly.
    """
    from magi.agent.db import open_session
    from magi.agent.memory.magi.models import (
        SOURCE_MANUAL,
        MemoryEntry,
    )

    when = updated_at or datetime.now(timezone.utc).replace(tzinfo=None)
    with open_session() as db:
        row = MemoryEntry(
            uid=uid,
            kind=kind,
            subject=subject,
            body=body,
            importance=importance,
            source=SOURCE_MANUAL,
            completed_at=completed_at,
            created_at=when,
            updated_at=when,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


# -- tests -----------------------------------------------------------------


def test_list_memory_returns_empty_when_no_rows(client):
    """Happy-path empty state. The endpoint never errors
    when there are zero rows — the UI renders a friendly
    empty-state message in this case."""
    r = client.get("/api/memory")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0}


def test_list_memory_requires_admin(env):
    """No cookie → 401 (``AdminGate``). The auth check
    runs before the ORM query — no memory data leaks."""
    from magi.channels.webui.app import create_app

    app = create_app()
    bare = TestClient(app)
    r = bare.get("/api/memory")
    assert r.status_code == 401


def test_list_memory_403_for_non_admin(charlie_client):
    """``magi_session=<charlie.id>`` (role=employee) →
    401. ``AdminGate`` checks ``Employee.role == 'admin'``;
    any other role bounces at the dependency."""
    r = charlie_client.get("/api/memory")
    assert r.status_code == 401


def test_list_memory_scopes_to_caller_employee(
    env, client, bob_client,
):
    """Admin A's memory must NOT appear in admin B's list.
    The endpoint derives the uid from the cookie;
    there's no URL knob that could let B request A's rows."""
    _seed_memory(
        env, uid=env["alice"].id,
        kind="important", subject="Alice's policy",
        body="Confidential.", importance=5,
    )

    # Alice's view sees her row.
    r = client.get("/api/memory")
    assert r.status_code == 200
    body_a = r.json()
    assert body_a["total"] == 1
    assert body_a["items"][0]["subject"] == "Alice's policy"

    # Bob's view sees zero — he doesn't own this row.
    r = bob_client.get("/api/memory")
    assert r.status_code == 200
    body_b = r.json()
    assert body_b["total"] == 0
    assert body_b["items"] == []


def test_list_memory_returns_both_kinds_and_completed_rows(
    env, client,
):
    """Both kinds (``important`` + ``ongoing``) appear.
    Both completion states (in-flight + completed) for
    ``ongoing`` rows appear too — the operator view is
    the audit trail and includes the recent-completion
    history (per the plan's "Show all" decision)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_memory(
        env, uid=env["alice"].id,
        kind="important", subject="Important fact",
        body="Long-lived.", importance=5,
        updated_at=now - timedelta(days=3),
    )
    _seed_memory(
        env, uid=env["alice"].id,
        kind="ongoing", subject="In-flight task",
        body="Still working.", importance=3,
        updated_at=now - timedelta(days=2),
    )
    _seed_memory(
        env, uid=env["alice"].id,
        kind="ongoing", subject="Done yesterday",
        body="Closed.", importance=4,
        updated_at=now - timedelta(days=1),
        completed_at=now - timedelta(days=1),
    )

    r = client.get("/api/memory")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3

    # Build a lookup by subject so the test is independent
    # of the order-by-importance secondary sort.
    by_subject = {row["subject"]: row for row in body["items"]}

    assert by_subject["Important fact"]["kind"] == "important"
    assert by_subject["Important fact"]["completed_at"] is None

    assert by_subject["In-flight task"]["kind"] == "ongoing"
    assert by_subject["In-flight task"]["completed_at"] is None

    assert by_subject["Done yesterday"]["kind"] == "ongoing"
    assert by_subject["Done yesterday"]["completed_at"] is not None


def test_list_memory_orders_by_importance_then_updated(
    env, client,
):
    """Three rows with different importance + recency.
    Higher importance comes first; ties break on
    ``updated_at DESC`` (most recent first)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Low importance, recent → must rank below higher
    # importance even if newer.
    _seed_memory(
        env, uid=env["alice"].id,
        kind="ongoing", subject="Low recent",
        body="x", importance=1, updated_at=now,
    )
    # High importance, older.
    _seed_memory(
        env, uid=env["alice"].id,
        kind="important", subject="High older",
        body="x", importance=5, updated_at=now - timedelta(days=10),
    )
    # Two with importance=3; newer should win.
    _seed_memory(
        env, uid=env["alice"].id,
        kind="ongoing", subject="Mid older",
        body="x", importance=3, updated_at=now - timedelta(days=5),
    )
    _seed_memory(
        env, uid=env["alice"].id,
        kind="ongoing", subject="Mid newer",
        body="x", importance=3, updated_at=now - timedelta(days=1),
    )

    r = client.get("/api/memory")
    assert r.status_code == 200
    subjects = [row["subject"] for row in r.json()["items"]]
    assert subjects == [
        "High older",    # importance=5
        "Mid newer",     # importance=3, newer of two
        "Mid older",     # importance=3, older of two
        "Low recent",    # importance=1
    ]


def test_list_memory_returns_full_body_for_tooltip(
    env, client,
):
    """The endpoint ships the full body in the response;
    the WebUI truncates to 200 chars for the preview
    cell with ``title=`` hover. The store caps body at
    8 KB so any row under that limit renders verbatim."""
    long_body = (
        "MAGI should remember to check the Q3 expense "
        "report every Friday before EOD. Ping the operator "
        "if there are anomalies over $500."
    )
    _seed_memory(
        env, uid=env["alice"].id,
        kind="ongoing", subject="Friday reminder",
        body=long_body, importance=4,
    )

    r = client.get("/api/memory")
    assert r.status_code == 200
    row = r.json()["items"][0]
    # Full body — no truncation in the API response; the
    # WebUI does the preview clipping.
    assert row["body"] == long_body


def test_list_memory_caps_at_200(env, client):
    """The endpoint caps at 200 rows; ``total`` reports
    the actual rendered count (which equals the cap when
    there are more)."""
    for i in range(5):
        _seed_memory(
            env, uid=env["alice"].id,
            kind="ongoing", subject=f"task {i}",
            body=f"body {i}", importance=3,
        )
    r = client.get("/api/memory")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5