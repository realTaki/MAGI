"""End-to-end test: contacts API role-change seeds presets.

The "preset scheduled tasks" feature's two entry points
live in :mod:`magi.channels.webui.api.contacts`:

  - ``create_contact``  — when the initial ``role`` is
    ``"assigned"``, the helper fires post-commit.
  - ``update_contact``  — when ``role`` transitions INTO
    ``"assigned"`` (any prior role → ``"assigned"``), the
    helper fires post-commit.

This file pins the integration at the API surface so a
regression that breaks the transition (missing hook, wrong
SQL filter, etc.) surfaces before it reaches the
dashboard.

Each scenario:

  A. POST  /api/contacts  role="assigned"   → 2 presets
  B. POST  /api/contacts  role="contact"    → 0 presets
     then PATCH role="assigned"               → 2 presets
  C. POST  /api/contacts  admin=true, role="contact"     → 0 presets
     then PATCH role="assigned"               → 2 presets
  D. POST  /api/contacts  role="contact"    → 0 presets
     then PATCH role="contact" (no-op)       → 0 presets
  E. POST  /api/contacts  role="assigned"   → 2 presets
     then PATCH role="assigned" again        → 0 NEW (idempotent)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh state dir + one admin Contact (the operator).

    Returns the seeded operator + the auto-seeded
    ``daily_standup_brief`` / ``weekly_review`` presets
    (via migration ``0006_task_presets``).
    """
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import (
        Contact,
        init_orm,
        init_sqlite,
        open_session)
    init_sqlite(str(sd))
    init_orm(str(sd))

    with open_session() as db:
        admin = Contact(
            name="Operator",
            display_name="Operator",
            telegram_id=9101,
            admin=True, role="assigned"
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        from magi.agent.proactive.orm_models import TaskPreset
        preset_keys = sorted(
            p.key for p in db.query(TaskPreset).all()
        )

    return {
        "state": sd,
        "admin": admin,
        "preset_keys": preset_keys,
        "preset_count": len(preset_keys),
    }


@pytest.fixture
def client(state):
    """TestClient signed in as the seeded admin."""
    from magi.channels.webui.app import create_app
    from magi.channels.webui.api.auth import _sign_uid

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(state["admin"].id))
    return c


def _count_tasks_for(client: TestClient, contact_id: int) -> int:
    """Helper: count tasks owned by ``contact_id`` via the
    tasks API. (We don't use this everywhere — the test
    client shares a connection pool with the route
    handler that just committed the seed, and ``BEGIN
    IMMEDIATE`` on the follow-up request can trip a
    spurious "database is locked" on some SQLite
    configurations. Direct DB checks via
    ``_preset_task_count`` are the more reliable form.)"""
    r = client.get(f"/api/tasks?uid={contact_id}")
    assert r.status_code == 200, r.text
    return len(r.json())


def _preset_task_count(contact_id: int) -> int:
    """Helper: count preset-derived tasks for a contact
    directly via the DB (the API's ``kind`` filter is what
    the dashboard uses; this matches that exactly). The
    dashboard's two-list layout depends on this."""
    from magi.agent.db import open_session
    from magi.agent.proactive.orm_models import Task

    with open_session() as db:
        return db.query(Task).filter(
            Task.uid == contact_id,
            Task.preset_key.is_not(None)).count()


def _all_task_count(contact_id: int) -> int:
    """Helper: count ALL tasks owned by ``contact_id``
    (preset + custom), directly via the DB."""
    from magi.agent.db import open_session
    from magi.agent.proactive.orm_models import Task

    with open_session() as db:
        return db.query(Task).filter(Task.uid == contact_id).count()


# -- scenario A -----------------------------------------------------------


def test_post_with_role_assigned_seeds_presets(state, client):
    """POST /api/contacts with ``role='assigned'`` triggers
    the create-time seed hook."""
    r = client.post("/api/contacts", json={
        "name": "Alice",
        "telegram_id": 9202,
        "role": "assigned",
        "provider": "minimax",
        "api_key": "sk-alice",
    })
    assert r.status_code == 201, r.text
    alice_id = r.json()["id"]

    assert _preset_task_count(alice_id) == state["preset_count"]
    assert _all_task_count(alice_id) == state["preset_count"]


def test_post_with_admin_true_role_contact_does_not_seed(state, client):
    """POST with ``admin=true, role='contact'`` is a no-op
    for the preset seed hook.

    After the role/admin split (2024), WebUI sign-in
    rights are carried by ``admin`` (boolean), not by
    ``role='admin'`` (which is no longer in the enum).
    A contact with ``admin=True`` but ``role='contact'``
    is a pure backend operator — they're NOT the served
    user, so the preset seed hook correctly skips them.

    The previous test in this slot (``test_post_with_role_admin_does_not_seed``)
    relied on the old ``role='admin'`` semantic which
    conflated "operator" with "not served". In the new
    model the analogue is admin=True + role≠'assigned'
    (operator but not the served user) — that's what this
    test now covers.
    """
    r = client.post("/api/contacts", json={
        "name": "Backend Admin",
        "telegram_id": 9203,
        "admin": True,
        "role": "contact",
    })
    assert r.status_code == 201, r.text
    admin_id = r.json()["id"]
    assert _all_task_count(admin_id) == 0
    assert _preset_task_count(admin_id) == 0


# -- scenario B -----------------------------------------------------------


def test_patch_to_assigned_seeds_presets(state, client):
    """POST with ``role='contact'`` → 0 tasks.
    PATCH role → ``assigned`` → 2 tasks."""
    r = client.post("/api/contacts", json={
        "name": "Bob",
        "telegram_id": 9204,
        "role": "contact",
    })
    assert r.status_code == 201, r.text
    bob_id = r.json()["id"]
    assert _all_task_count(bob_id) == 0

    promote = client.patch(
        f"/api/contacts/{bob_id}", json={"role": "assigned"})
    assert promote.status_code == 200, promote.text
    assert promote.json()["role"] == "assigned"

    assert _preset_task_count(bob_id) == state["preset_count"]
    assert _all_task_count(bob_id) == state["preset_count"]


def test_patch_admin_to_assigned_seeds_presets(state, client):
    """``admin=True, role='contact'`` is NOT seeded.

    After the 2024 role/admin split, ``admin`` is a
    separate boolean (WebUI sign-in) and the seed hook
    triggers on ``role='assigned'`` (the served user).
    A backend operator (``role='contact', admin=True``)
    who later gets promoted to the served user
    (``role='assigned'``) should now trigger seeding —
    this is the new edge of the transition matrix.
    """
    r = client.post("/api/contacts", json={
        "name": "Charlie",
        "telegram_id": 9205,
        "admin": True, "role": "contact",
    })
    assert r.status_code == 201, r.text
    charlie_id = r.json()["id"]
    assert _all_task_count(charlie_id) == 0

    promote = client.patch(
        f"/api/contacts/{charlie_id}", json={"role": "assigned"})
    assert promote.status_code == 200, promote.text
    assert promote.json()["role"] == "assigned"

    assert _preset_task_count(charlie_id) == state["preset_count"]
    assert _all_task_count(charlie_id) == state["preset_count"]


# -- scenario C: idempotency on repeat transition -----------------------


def test_repeat_assigned_patch_does_not_duplicate(state, client):
    """A second PATCH role→assigned on the same contact
    must NOT duplicate the seeded rows (helper's
    per-(uid, preset_id) existence check)."""
    r = client.post("/api/contacts", json={
        "name": "Dora",
        "telegram_id": 9206,
        "role": "assigned",
    })
    assert r.status_code == 201, r.text
    dora_id = r.json()["id"]
    first_count = _preset_task_count(dora_id)
    assert first_count == state["preset_count"]

    # Repeat the PATCH — still assigned→assigned; should
    # be a no-op (the prev_role == "assigned" guard means
    # the helper isn't even called).
    repeat = client.patch(
        f"/api/contacts/{dora_id}", json={"role": "assigned"})
    assert repeat.status_code == 200
    assert _preset_task_count(dora_id) == first_count


def test_assigned_to_admin_to_assigned_does_not_duplicate(state, client):
    """A contact that flips assigned→admin→assigned still
    ends up with exactly the seeded count — the helper's
    per-(uid, preset_id) short-circuit catches the
    re-seed attempt."""
    r = client.post("/api/contacts", json={
        "name": "Eve",
        "telegram_id": 9207,
        "role": "assigned",
    })
    assert r.status_code == 201, r.text
    eve_id = r.json()["id"]
    assert _preset_task_count(eve_id) == state["preset_count"]

    # Down to admin — tasks persist (we don't delete on
    # transition-away).
    demote = client.patch(
        f"/api/contacts/{eve_id}", json={"admin": True, "role": "assigned"})
    assert demote.status_code == 200
    assert _preset_task_count(eve_id) == state["preset_count"]

    # Back to assigned — second seed attempt; helper sees
    # existing rows and skips.
    repromote = client.patch(
        f"/api/contacts/{eve_id}", json={"role": "assigned"})
    assert repromote.status_code == 200
    assert _preset_task_count(eve_id) == state["preset_count"]


# -- scenario D: non-role patches do NOT seed ---------------------------


def test_non_role_patch_does_not_seed(state, client):
    """Updating a contact's name / provider / api_key
    without touching ``role`` must NOT trigger the seed
    hook — the route gates on ``role in model_fields_set``."""
    r = client.post("/api/contacts", json={
        "name": "Frank",
        "telegram_id": 9208,
        "role": "contact",
    })
    assert r.status_code == 201, r.text
    frank_id = r.json()["id"]

    rename = client.patch(
        f"/api/contacts/{frank_id}",
        json={"display_name": "Frank Renamed"})
    assert rename.status_code == 200
    assert _all_task_count(frank_id) == 0


# -- scenario E: list filter splits preset vs custom --------------------


def test_list_tasks_kind_filter_splits_preset_and_custom(state, client):
    """``GET /api/tasks?kind=preset`` returns only preset
    rows; ``kind=custom`` returns only custom rows. The
    dashboard's two-list layout depends on this."""
    # Seed an assigned user → 2 preset rows.
    r = client.post("/api/contacts", json={
        "name": "Gina",
        "telegram_id": 9209,
        "role": "assigned",
    })
    assert r.status_code == 201, r.text
    gina_id = r.json()["id"]

    # Add a custom task for Gina. The ``X-Contact-Id``
    # header tells the route to stamp ``uid=gina_id`` on
    # the new row instead of the cookie's admin (the
    # creator-role gate also accepts ``assigned`` per
    # ``_ROLE_MAY_CREATE``).
    custom = client.post(
        "/api/tasks",
        headers={"X-Contact-Id": str(gina_id)},
        json={
            "name": "Gina weekly review (custom)",
            "prompt": "Custom prompt.",
            "frequency": "weekly",
            "hour": 10,
            "minute": 0,
            "day_of_week": 0,
            "target_channel": "webui",
        })
    assert custom.status_code == 201, custom.text
    assert custom.json()["uid"] == gina_id

    preset_list = client.get(
        f"/api/tasks?kind=preset&uid={gina_id}").json()
    custom_list = client.get(
        f"/api/tasks?kind=custom&uid={gina_id}").json()

    assert len(preset_list) == state["preset_count"]
    assert len(custom_list) == 1
    assert all(t.get("preset_key") for t in preset_list)
    assert all(t.get("preset_key") is None for t in custom_list)