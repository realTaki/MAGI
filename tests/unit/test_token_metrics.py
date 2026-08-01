"""End-to-end tests for ``GET /api/contacts/{uid}/token-usage``.

The endpoint aggregates token usage for one Contact across
three periods (week / month / total) in a single round-trip.
Pinned:

  1. **Empty result shape** — no rows → zeros + ISO timestamps.
  2. **Aggregation correctness** — sum of input / output /
     call_count for inserted rows that fall in / out of each
     window.
  3. **Auth gate** — non-admin cookie → 401; unsigned
     cookie → 401.
  4. **Timezone echo** — the ``timezone`` field mirrors
     ``get_system_timezone(state_dir)`` so the UI can show
     the active tz.
  5. **Period timestamps** — week/month/total anchors are
     non-empty ISO 8601 strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

def _signed_session_cookie(uid: int) -> str:
    """Mint an HMAC-signed ``magi_session`` cookie value."""
    from magi.channels.webui.api.auth import _sign_uid

    return _sign_uid(uid)

def _iso_utc(d: datetime) -> str:
    """Convert to the same naive-UTC ISO format the ORM uses."""
    return d.astimezone(timezone.utc).replace(tzinfo=None).isoformat()

@pytest.fixture
def env(monkeypatch, tmp_path):
    """MAGI_STATE_DIR + ORM + two admins (one for the
    authed caller, one whose token usage we aggregate)."""
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
        TokenUsage,
        init_orm,
        init_sqlite,
        open_session)

    init_sqlite(str(state))
    init_orm(str(state))

    # Pin the system timezone to UTC so the aggregation
    # endpoint's ``week`` / ``month`` window boundaries are
    # deterministic regardless of the test host's local tz.
    # Without this, a host running in a non-UTC zone (e.g.
    # MDT or CST) shifts the Monday-00:00-local anchor and
    # rows that the test thinks are "1 day ago in UTC"
    # fall outside the configured window.
    from magi.channels.webui.api.system_settings import set_system_timezone
    set_system_timezone(str(state), "UTC")

    with open_session() as db:
        admin = Contact(
            name="Alice",
            telegram_id=9001,
            admin=True, role="assigned"
        )
        target = Contact(
            name="Bob",
            telegram_id=9002,
            role="assigned"
        )
        db.add_all([admin, target])
        db.commit()
        db.refresh(admin)
        db.refresh(target)

    return {
        "state": state,
        "admin": admin,
        "target": target,
    }

@pytest.fixture
def client(env):
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _signed_session_cookie(env["admin"].id))
    return c

def _seed_usage(uid: int, ts: datetime, in_tok: int, out_tok: int):
    """Insert one TokenUsage row directly.

    ``channel`` / ``provider`` are ``NOT NULL`` columns on
    the table — the agent loop always writes them; tests
    use the same shape.
    """
    from magi.db import TokenUsage, open_session

    with open_session() as db:
        db.add(
            TokenUsage(
                uid=uid,
                ts=ts.replace(tzinfo=None),
                input_tokens=in_tok,
                output_tokens=out_tok,
                channel="webui",
                provider="minimax-cn",
                model="claude-test")
        )
        db.commit()

# -- tests -----------------------------------------------------------------

def test_empty_returns_zeros(client, env):
    """No usage rows → zero totals; all three periods
    populated with non-empty ISO timestamps."""
    r = client.get(f"/api/contacts/{env['target'].id}/token-usage")
    assert r.status_code == 200
    body = r.json()
    assert body["uid"] == env["target"].id
    for key in ("week", "month", "total"):
        assert body[key]["input_tokens"] == 0
        assert body[key]["output_tokens"] == 0
        assert body[key]["call_count"] == 0
        assert isinstance(body[key]["period_start"], str) and body[key]["period_start"]
        assert isinstance(body[key]["period_end"], str) and body[key]["period_end"]
    assert isinstance(body["timezone"], str) and body["timezone"]

def test_total_aggregates_all_rows(client, env):
    """Two rows in the past 7 days → ``total`` sums them
    (regardless of whether week/month cut them off)."""
    now = datetime.now(timezone.utc)
    _seed_usage(env["target"].id, now - timedelta(days=2), in_tok=100, out_tok=50)
    _seed_usage(env["target"].id, now - timedelta(days=4), in_tok=300, out_tok=80)

    r = client.get(f"/api/contacts/{env['target'].id}/token-usage")
    assert r.status_code == 200
    body = r.json()
    assert body["total"]["input_tokens"] == 400
    assert body["total"]["output_tokens"] == 130
    assert body["total"]["call_count"] == 2

def test_week_window_includes_recent(client, env):
    """Rows inside the Monday-anchored week window sum into
    ``week``; rows from last week do not.

    The ``week`` window is "Monday 00:00 in the configured
    timezone → now", so we seed two rows on opposite sides
    of that boundary:

      - 2 hours ago (always inside this week).
      - 10 days ago (always outside — that's at least one
        full week back, plus several days of buffer).
    """
    now = datetime.now(timezone.utc)
    _seed_usage(env["target"].id, now - timedelta(hours=2), in_tok=10, out_tok=5)
    _seed_usage(env["target"].id, now - timedelta(days=10), in_tok=99, out_tok=99)

    r = client.get(f"/api/contacts/{env['target'].id}/token-usage")
    assert r.status_code == 200
    body = r.json()
    assert body["week"]["call_count"] == 1
    assert body["week"]["input_tokens"] == 10
    # Total includes both.
    assert body["total"]["call_count"] == 2

def test_scope_to_one_uid(client, env):
    """Aggregation filters by the URL's uid; other Contacts'
    rows aren't counted."""
    from magi.db import Contact, open_session as _os

    with _os() as db:
        other = Contact(
            name="Other",
            telegram_id=9003,
            role="assigned"
        )
        db.add(other)
        db.commit()
        db.refresh(other)

    now = datetime.now(timezone.utc)
    _seed_usage(env["target"].id, now - timedelta(hours=6), in_tok=100, out_tok=50)
    _seed_usage(other.id, now - timedelta(hours=2), in_tok=999, out_tok=999)

    r = client.get(f"/api/contacts/{env['target'].id}/token-usage")
    assert r.json()["total"]["input_tokens"] == 100  # other.uid NOT counted

def test_unauthorized_without_cookie(client):
    """No cookie at all → 401 (admin_gate)."""
    raw = TestClient(client.app)
    r = raw.get("/api/contacts/1/token-usage")
    assert r.status_code == 401

def test_unauthorized_unsigned_cookie(client, env):
    """A junk cookie value → 401. (The relaxed test-mode
    verifier (conftest.py) accepts naked ints, so to verify
    the gate rejects non-cookie junk we pass a string the
    verifier cannot parse.)"""
    raw = TestClient(client.app)
    raw.cookies.set("magi_session", "junk-not-a-cookie")
    r = raw.get(f"/api/contacts/{env['target'].id}/token-usage")
    assert r.status_code == 401

def test_unknown_contact_still_returns_200(client, env):
    """The endpoint doesn't 404 on a non-existent contact —
    it returns zeros (the SELECT just matches no rows)."""
    r = client.get("/api/contacts/99999/token-usage")
    assert r.status_code == 200
    body = r.json()
    assert body["uid"] == 99999
    assert body["total"]["call_count"] == 0
