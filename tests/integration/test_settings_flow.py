"""End-to-end test for the **Settings tab** business flow.

The dashboard's Settings tab renders five sub-cards, each
backed by a distinct API. Real operator journeys frequently
visit several in one session (set the persona, then the
timezone, then re-bind a contact, then realise they need
to reset the persona). This file pins the cross-card
invariants that surface from those journeys:

  1. **Soul (persona) — load / save / reset** — the
     operator's edits to ``SOUL.md`` land on disk, and a
     ``POST /api/soul/reset`` restores the bundled default.

  2. **System timezone — load / save / re-read** — the
     tz edit takes effect on the next read; the
     cached ``_resolve_system_tz`` invalidation hook
     (added in this round) means a freshly saved tz is
     immediately reflected in any task-creation call.

  3. **Cross-flow: tz change → Task row stores new tz** —
     the operator changes the system tz, creates a new
     task, and the task row's ``tz`` column reflects the
     new value (not the stale cached one).

The soul + tz together cover two of the most-mutated
``settings``-backed surfaces; the cross-flow test pins the
fix for the nested-BEGIN-IMMEDIATE deadlock in
``create_task`` + ``_resolve_system_tz`` (the regression
that these tests were written to catch).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# -- fixtures --------------------------------------------------------------

@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh state dir + workspace + one admin Contact."""
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    
    import magi.bus.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.bus.models.local.contact import Contact
    from magi.bus.db import (
        init_orm,
        init_sqlite,
        open_session,
    )
    init_sqlite(str(sd))
    init_orm(str(sd))
    with open_session() as db:
        admin = Contact(
            name="Alice",
            display_name="Alice",
            telegram_id=9101,
            admin=True, role="assigned"
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return {"state": sd, "workspace": ws, "admin": admin}

@pytest.fixture
def client(state):
    """TestClient signed in as Alice (admin)."""
    from magi.channels.api.app import create_app
    from magi.channels.api.auth import _sign_uid

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(state["admin"].id))
    return c

# -- soul (persona) -------------------------------------------------------

def test_soul_load_save_reset_round_trip(state, client):
    """The operator edits ``SOUL.md`` via the Settings tab:

      1. ``GET /api/soul`` → bundled fallback (``is_bundled_fallback=True``).
      2. ``PUT /api/soul`` with new content → file on disk updated.
      3. ``GET /api/soul`` → returns the new content, ``is_bundled_fallback=False``.
      4. ``POST /api/soul/reset`` → restores the bundled default.
      5. ``GET /api/soul`` → bundled fallback again.

    Pins the dashboard's persona-editor flow: a refactor that
    breaks the atomic-write path (so a partial save corrupts
    the file) or the reset-to-default path (so the operator
    can't undo their edits) trips this test before the
    dashboard does.
    """
    # 1. Pristine — bundled fallback.
    initial = client.get("/api/soul").json()
    assert initial["is_bundled_fallback"] is True
    assert initial["modified_at"] is None
    fallback_content = initial["content"]
    assert len(fallback_content) > 0

    # 2. Save a new persona.
    new_persona = "You are an expert contract negotiator. Always speak in English."
    save = client.put(
        "/api/soul",
        json={"content": new_persona})
    assert save.status_code == 200, save.text
    saved = save.json()
    assert "modified_at" in saved and saved["modified_at"]

    # 3. Read back — the on-disk truth.
    after = client.get("/api/soul").json()
    assert after["content"].strip() == new_persona
    assert after["is_bundled_fallback"] is False
    assert after["modified_at"] == saved["modified_at"]

    # 4. Reset to bundled default. The endpoint returns
    # only ``modified_at`` — the next GET surfaces the
    # actual content.
    reset = client.post("/api/soul/reset")
    assert reset.status_code == 200, reset.text
    assert "modified_at" in reset.json()

    # 5. Confirm the file was rewritten to the bundled
    # default. The "first-time" fallback served when no
    # ``SOUL.md`` exists is a separate string from the
    # bundled ``SOUL.md`` content — ``reset_soul`` writes
    # the latter, so the post-reset content matches what
    # the file actually contains, not the first-load
    # fallback we read above.
    after_reset = client.get("/api/soul").json()
    assert after_reset["is_bundled_fallback"] is False
    # Content differs from the user-saved persona.
    assert after_reset["content"].strip() != new_persona

# -- system timezone -----------------------------------------------------

def test_timezone_load_save_reread_round_trip(state, client):
    """The operator changes the system timezone via the
    Settings tab and immediately sees the change on the
    next ``GET /api/system-settings/timezone``. This is
    the same call pattern the token-bill aggregation
    endpoint uses on every request, so a stale-cache
    regression would surface as "today's bill shows
    yesterday's tz boundary".
    """
    # 1. Pristine — defaults to the server's local tz.
    initial = client.get("/api/system-settings/timezone").json()
    assert initial["current"] == initial["default"]
    assert "Asia/Tokyo" in initial["choices"]
    assert "America/Los_Angeles" in initial["choices"]

    # 2. Save a new tz.
    new_tz = "Asia/Tokyo"
    save = client.put(
        "/api/system-settings/timezone",
        json={"timezone": new_tz})
    assert save.status_code == 200, save.text
    assert save.json()["current"] == new_tz

    # 3. Read back — the new value, not the cached default.
    after = client.get("/api/system-settings/timezone").json()
    assert after["current"] == new_tz
    assert after["default"] != after["current"]

    # 4. An unknown tz returns 400 (the dashboard relies
    # on this so a stale client doesn't silently fall
    # back to UTC).
    bad = client.put(
        "/api/system-settings/timezone",
        json={"timezone": "Atlantis/Avalon"})
    assert bad.status_code == 400
    assert bad.json()["code"] == "validation.unknown_timezone"

    # 5. The good value is still persisted after a failed
    # invalid write (the operator's previous choice is
    # preserved, not clobbered).
    still_good = client.get("/api/system-settings/timezone").json()
    assert still_good["current"] == new_tz

# -- cross-flow: tz change is reflected in a new task ---------------------

def test_timezone_change_propagates_to_newly_created_task(
    state, client):
    """After the operator changes the system timezone, the
    next task they create stores the new ``tz`` — NOT the
    cached value from a previous request.

    Pins the cache-invalidation path added in this round:
    ``PUT /api/system-settings/timezone`` calls
    ``_invalidate_system_tz_cache`` so the next
    ``_resolve_system_tz()`` (called by ``POST /api/tasks``)
    re-reads from the KV store instead of returning the
    stale cache.

    Without that invalidation, the dashboard's tz edit
    wouldn't reach the Task row until the process restarts.
    """
    # 1. Save a non-default tz.
    target_tz = "Europe/Berlin"
    client.put(
        "/api/system-settings/timezone",
        json={"timezone": target_tz})

    # 2. Create a task. The server-stamped ``tz`` on the
    # row must reflect the new value, not the cached
    # pre-edit default.
    create = client.post("/api/tasks", json={
        "name": "tz propagation check",
        "prompt": "ping",
        "frequency": "daily",
        "hour": 9,
        "minute": 0,
        "channel": "webui",
    })
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["tz"] == target_tz

    # 3. The listing endpoint reads tz through the same
    # helper, so the GET round-trip also reflects the
    # new value (not the stale default).
    listing = client.get("/api/tasks").json()
    ours = next(t for t in listing if t["id"] == body["id"])
    assert ours["tz"] == target_tz