"""Unit tests for :mod:`magi.agent.proactive.presets`.

Covers the four invariants that drive the seed-on-create
feature:

  1. **Idempotency** — re-running the helper against the
     same contact must not duplicate the rows.
  2. **Role gate** — only ``role='assigned'`` contacts
     get seeded; ``admin`` / ``contact`` / ``guest`` are
     skipped (the call sites in :mod:`magi.channels.webui.api.contacts`
     also gate, but a defensive guard inside the helper
     means a future caller can't accidentally seed the
     wrong role).
  3. **Disabled-preset skip** — flipping
     ``TaskPreset.enabled=0`` causes the helper to skip
     that template on the next call.
  4. **Snapshot semantics** — editing the preset template
     after seed must NOT mutate the existing per-user
     ``Task`` rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh state dir + the bundled default presets.

    ``init_orm`` synchronises the YAML files under
    ``prompts/task_presets`` after the schema is ready.
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

    admin = Contact(
        name="Operator",
        telegram_id=9101,
        admin=True, role="assigned"
    )
    with open_session() as db:
        db.add(admin)
        db.commit()
        db.refresh(admin)
        # Sanity: the migration's seed is present.
        from magi.agent.proactive.orm_models import TaskPreset
        presets = db.query(TaskPreset).order_by(TaskPreset.key).all()
    return {
        "state": sd,
        "admin": admin,
        "preset_count": len(presets),
        "preset_keys": [p.key for p in presets],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_bundled_templates_are_loaded_and_resynchronised(state):
    """A source-file edit wins over a stale bundled DB row on next boot."""
    from magi.agent.db import open_session
    from magi.agent.proactive.orm_models import TaskPreset
    from magi.agent.proactive.preset_templates import (
        load_task_preset_templates,
        sync_bundled_task_presets,
    )

    templates = {template.key: template for template in load_task_preset_templates()}
    assert set(templates) == set(state["preset_keys"])

    with open_session() as db:
        row = db.query(TaskPreset).filter_by(key="morning_brief").one()
        row.prompt = "temporary developer override"
        db.commit()

    with open_session() as db:
        created, updated = sync_bundled_task_presets(db)
        db.commit()
        row = db.query(TaskPreset).filter_by(key="morning_brief").one()

    assert created == 0
    assert updated == 1
    assert row.prompt == templates["morning_brief"].prompt


def test_seed_inserts_a_task_per_enabled_preset_for_assigned_contact(state):
    """A fresh ``assigned`` contact gets one Task row per
    enabled preset. v0 ships 4 defaults (daily_standup_brief,
    weekly_review, morning_brief, night_summary); the count
    rides on ``state["preset_count"]`` so this test stays
    in sync as new presets are added."""
    from magi.agent.db import open_session
    from magi.agent.proactive.presets import seed_presets_for_contact
    from magi.agent.proactive.orm_models import Task, TaskPreset

    with open_session() as db:
        alice = Contact(name="Alice", role="assigned", telegram_id=9202)
        db.add(alice)
        db.flush()
        alice_id = alice.id
        inserted = seed_presets_for_contact(db, alice_id)
        db.commit()
    assert inserted == state["preset_count"]
    # All four defaults are seeded; verify by sorted key set.
    assert sorted(state["preset_keys"]) == sorted([
        "daily_standup_brief",
        "weekly_review",
        "morning_brief",
        "night_summary",
    ])

    # Verify: alice has one row per preset, each carrying
    # the back-pointer fields.
    with open_session() as db:
        rows = db.query(Task).filter(Task.uid == alice_id).order_by(
            Task.preset_key
        ).all()
        keys = sorted(r.preset_key for r in rows)
        assert keys == sorted(state["preset_keys"])
        assert all(r.preset_id is not None for r in rows)
        assert all(r.preset_key is not None for r in rows)
        assert all(r.target_channel == "tg" for r in rows)
        # The per-user name is disambiguated with the
        # contact's label — see the long docstring on
        # ``_build_task_from_preset`` for why.
        assert all("(Alice)" in r.name for r in rows)


def test_seed_is_idempotent_on_repeat_call(state):
    """Calling the helper twice for the same contact must
    return 0 on the second call (per-preset existence
    short-circuit) and leave the row count unchanged."""
    from magi.agent.db import open_session
    from magi.agent.proactive.presets import seed_presets_for_contact
    from magi.agent.proactive.orm_models import Task

    with open_session() as db:
        alice = Contact(name="Alice", role="assigned", telegram_id=9202)
        db.add(alice); db.flush()
        first = seed_presets_for_contact(db, alice.id)
        db.commit()
    assert first == state["preset_count"]

    with open_session() as db:
        second = seed_presets_for_contact(db, alice.id)
        db.commit()
    assert second == 0

    with open_session() as db:
        rows = db.query(Task).filter(Task.uid == alice.id).all()
        # Still exactly the preset_count rows, no duplicates.
        assert len(rows) == state["preset_count"]


def test_seed_skips_admin_contacts(state):
    """``role='guest', admin=True`` is a no-op for the
    preset seed hook — guards against the helper being
    called from a future code path that forgets the
    role-vs-admin distinction.

    After the 2024 role/admin split, the seed hook
    triggers on ``role='assigned'`` (the served user).
    ``admin=True`` (WebUI operator) does NOT trigger
    seeding by itself — a backend operator who isn't
    the served user (``role='guest', admin=True``)
    doesn't need auto-seeded daily briefings.
    """
    from magi.agent.db import open_session
    from magi.agent.proactive.presets import seed_presets_for_contact
    from magi.agent.proactive.orm_models import Task

    with open_session() as db:
        admin2 = Contact(name="Admin2", admin=True, role='guest', telegram_id=9203)
        db.add(admin2); db.flush()
        n = seed_presets_for_contact(db, admin2.id)
        db.commit()
    assert n == 0

    with open_session() as db:
        rows = db.query(Task).filter(Task.uid == admin2.id).all()
        assert rows == []


def test_seed_skips_disabled_presets(state):
    """A preset with ``enabled=0`` is skipped on the next
    call. Existing per-user rows from before the disable
    keep their ``enabled`` bit (snapshot semantics — the
    helper reads the preset's current ``enabled`` only to
    decide whether to SEED, not to flip already-seeded
    rows)."""
    from magi.agent.db import open_session
    from magi.agent.proactive.presets import seed_presets_for_contact
    from magi.agent.proactive.orm_models import Task, TaskPreset

    with open_session() as db:
        # Disable everything except the daily preset so the
        # "skipped" assertion is unambiguous regardless of
        # how many presets are seeded at boot.
        for key in ("weekly_review", "morning_brief", "night_summary"):
            row = db.query(TaskPreset).filter(
                TaskPreset.key == key
            ).one()
            row.enabled = 0
        db.commit()

    with open_session() as db:
        alice = Contact(name="Alice", role="assigned", telegram_id=9202)
        db.add(alice); db.flush()
        n = seed_presets_for_contact(db, alice.id)
        db.commit()
    # Only the daily preset is enabled → only 1 row.
    assert n == 1

    with open_session() as db:
        rows = db.query(Task).filter(Task.uid == alice.id).all()
        assert len(rows) == 1
        assert rows[0].preset_key == "daily_standup_brief"


def test_seed_snapshot_semantics_edits_preset_after_seed(state):
    """Editing a preset AFTER seeding must NOT mutate the
    already-seeded per-user rows. The per-user ``Task``
    row keeps its snapshotted ``prompt``."""
    from magi.agent.db import open_session
    from magi.agent.proactive.presets import seed_presets_for_contact
    from magi.agent.proactive.orm_models import Task, TaskPreset

    # 1. Seed an assigned contact.
    with open_session() as db:
        alice = Contact(name="Alice", role="assigned", telegram_id=9202)
        db.add(alice); db.flush()
        seed_presets_for_contact(db, alice.id)
        db.commit()

    # 2. Edit the preset's prompt in the DB.
    with open_session() as db:
        wp = db.query(TaskPreset).filter(
            TaskPreset.key == "daily_standup_brief"
        ).one()
        original = wp.prompt
        wp.prompt = "EDITED — this is the new template prompt."
        db.commit()

    # 3. Verify: alice's seeded row still has the original
    # prompt, not the edited one.
    with open_session() as db:
        row = db.query(Task).filter(
            Task.uid == alice.id,
            Task.preset_key == "daily_standup_brief").one()
        assert row.prompt == original
        assert "EDITED" not in row.prompt


def test_seed_returns_zero_for_unknown_contact(state):
    """Defensive: if the contact row was deleted between
    the call-site check and the helper invocation, the
    helper returns 0 and logs a warning. No FK violation
    surfaces to the route handler."""
    from magi.agent.db import open_session
    from magi.agent.proactive.presets import seed_presets_for_contact

    with open_session() as db:
        n = seed_presets_for_contact(db, 99999)
    assert n == 0


# -- imports shim so Contact is in scope for the test bodies above ---------
# ``Contact`` is referenced unqualified in each test body
# (e.g. ``Contact(name="Alice", role="assigned", ...)``).
# Keep the import at module scope so the linter doesn't
# complain and so the bodies read cleanly. The first test
# pulls it explicitly; the others rely on this namespace
# binding.
from magi.agent.db import Contact  # noqa: E402,F401  (used in test bodies)
