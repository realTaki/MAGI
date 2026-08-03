"""Tests for the daily-note feature.

Three surfaces pinned:

  1. **Store** — :func:`ContactStore.upsert_daily_note` does the
     append-or-insert correctly; :func:`read_daily_note` returns
     today's row (or ``None``); permanent notes still work
     unchanged (regression).
  2. **Partial unique index** — the
     ``ux_contact_notes_daily`` index rejects a hand-inserted
     duplicate ``(contact_id, note_date, kind='daily')`` pair.
  3. **System prompt** — the daily-note block folds into
     :func:`build_system_prompt` when ``show_daily_note`` is on
     (default), is empty when off, and folds in the capture
     rules only when ``show_daily_note_prompt`` is on.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def store_env(monkeypatch, tmp_path):
    """Fresh state dir + ORM + a single assigned contact."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.db import (
        Contact, init_orm, init_sqlite, open_session,
    )
    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as db:
        db.add(Contact(
            name="TA-daily",
            telegram_id=91001,
            admin=True,
            role="assigned",
        ))
        db.commit()

    return state


def _store(state):
    from bus.services.contact import ContactStore
    return ContactStore(str(state))


# --------------------------------------------------------------------------- #
# 1. upsert + read
# --------------------------------------------------------------------------- #


def test_upsert_daily_note_first_call_inserts(store_env):
    s = _store(store_env)
    from magi.db import open_session
    from magi.db.models_contact import ContactNote

    view = s.upsert_daily_note(contact_id=1, body_delta="sent the Q3 invoice")
    assert view.note == "sent the Q3 invoice"

    # Exactly one row exists, kind='daily', note_date=today UTC midnight.
    with open_session() as db:
        rows = db.query(ContactNote).filter_by(
            contact_id=1, kind="daily",
        ).all()
    assert len(rows) == 1
    assert rows[0].note == "sent the Q3 invoice"
    assert rows[0].note_date is not None
    assert rows[0].note_date.year == datetime.utcnow().year


def test_upsert_daily_note_second_call_appends(store_env):
    s = _store(store_env)
    s.upsert_daily_note(contact_id=1, body_delta="first delta")
    view = s.upsert_daily_note(contact_id=1, body_delta="second delta")
    assert view.note == "first delta\nsecond delta"

    # Still one row.
    from magi.db import open_session
    from magi.db.models_contact import ContactNote

    with open_session() as db:
        rows = db.query(ContactNote).filter_by(
            contact_id=1, kind="daily",
        ).all()
    assert len(rows) == 1
    assert rows[0].note == "first delta\nsecond delta"


def test_read_daily_note_returns_today(store_env):
    s = _store(store_env)
    assert s.read_daily_note(contact_id=1) is None
    s.upsert_daily_note(contact_id=1, body_delta="hi")
    note = s.read_daily_note(contact_id=1)
    assert note is not None
    assert note.note == "hi"


def test_upsert_daily_note_accepts_explicit_date(store_env):
    """Backfilling a missed day is supported — the LLM can pass
    ``note_date='YYYY-MM-DD'`` and the row lands on that day."""
    s = _store(store_env)
    yesterday = datetime.utcnow() - timedelta(days=1)
    yesterday_midnight = yesterday.replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    s.upsert_daily_note(
        contact_id=1,
        body_delta="back-filled",
        note_date=yesterday_midnight,
    )
    note = s.read_daily_note(
        contact_id=1, note_date=yesterday_midnight,
    )
    assert note is not None
    assert note.note == "back-filled"


def test_permanent_notes_unaffected_by_daily_upsert(store_env):
    """``add_note`` writes a permanent row with ``kind='permanent'``;
    ``upsert_daily_note`` does NOT collide with it. Both kinds
    coexist under the same contact."""
    s = _store(store_env)
    perm = s.add_note(contact_id=1, note="Lily 在财务部")
    daily = s.upsert_daily_note(contact_id=1, body_delta="today's delta")

    from magi.db import open_session
    from magi.db.models_contact import ContactNote

    with open_session() as db:
        rows = db.query(ContactNote).filter_by(contact_id=1).all()
    assert len(rows) == 2
    kinds = {r.kind for r in rows}
    assert kinds == {"permanent", "daily"}
    # Permanent has note_date=None; daily has today's midnight.
    for r in rows:
        if r.kind == "permanent":
            assert r.note_date is None
        else:
            assert r.note_date is not None
    assert perm.kind == "permanent"
    assert daily.kind == "daily"


def test_list_daily_notes_recent_first(store_env):
    """``list_daily_notes`` returns recent daily rows newest-first."""
    s = _store(store_env)
    today = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    s.upsert_daily_note(contact_id=1, body_delta="t-2", note_date=today - timedelta(days=2))
    s.upsert_daily_note(contact_id=1, body_delta="t-1", note_date=today - timedelta(days=1))
    s.upsert_daily_note(contact_id=1, body_delta="today", note_date=today)

    rows = s.list_daily_notes(contact_id=1, limit=10)
    assert [r.note for r in rows] == ["today", "t-1", "t-2"]


# --------------------------------------------------------------------------- #
# 2. Partial unique index — direct INSERT must fail on duplicate
# --------------------------------------------------------------------------- #


def test_partial_unique_index_rejects_duplicate_daily(store_env):
    """The ``ux_contact_notes_daily`` partial unique index
    (kind='daily') rejects a second row with the same
    ``(contact_id, note_date)``. Permanent rows are exempt
    from the constraint (note_date IS NULL is allowed for
    many rows)."""
    from magi.db import open_session
    from magi.db.models_contact import ContactNote
    from sqlalchemy.exc import IntegrityError

    s = _store(store_env)
    s.upsert_daily_note(contact_id=1, body_delta="first")
    # Direct INSERT bypasses the upsert path — confirm the
    # index actually rejects the duplicate.
    today = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    with open_session() as db:
        with pytest.raises(IntegrityError):
            db.add(ContactNote(
                contact_id=1,
                note="hand-inserted duplicate",
                source="eve",
                kind="daily",
                note_date=today,
            ))
            db.commit()


def test_partial_unique_index_allows_many_permanent(store_env):
    """Permanent rows are not constrained by the partial index
    (the WHERE clause excludes them)."""
    from magi.db import open_session
    from magi.db.models_contact import ContactNote

    s = _store(store_env)
    for i in range(3):
        s.add_note(contact_id=1, note=f"perm-{i}")
    with open_session() as db:
        n = db.query(ContactNote).filter_by(
            contact_id=1, kind="permanent",
        ).count()
    assert n == 3


# --------------------------------------------------------------------------- #
# 3. System prompt integration
# --------------------------------------------------------------------------- #


def test_system_prompt_folds_daily_note_block_by_default(store_env):
    """With no settings stored, ``build_system_prompt`` includes
    the daily-note block (when the contact has a daily row).
    """
    s = _store(store_env)
    s.upsert_daily_note(contact_id=1, body_delta="today's note")

    from magi.agent.system_prompt import build_system_prompt
    from magi.prompts import load_soul

    rendered = build_system_prompt(
        str(store_env), uid=1, soul=load_soul(),
    )
    assert "今日 daily_note" in rendered
    assert "today's note" in rendered


def test_system_prompt_omits_daily_block_when_disabled(store_env):
    """``system.show_daily_note=false`` mutes the daily-note block
    even when today's row has content."""
    s = _store(store_env)
    s.upsert_daily_note(contact_id=1, body_delta="today's note")

    from magi.db.settings import state_set as _state_set
    _state_set(str(store_env), "system.show_daily_note", "false")

    from magi.agent.system_prompt import build_system_prompt
    from magi.prompts import load_soul

    rendered = build_system_prompt(
        str(store_env), uid=1, soul=load_soul(),
    )
    assert "今日 daily_note" not in rendered


def test_system_prompt_folds_capture_rules_only_when_opted_in(store_env):
    """``system.show_daily_note_prompt=true`` folds the
    ``prompts/context/daily_note.md`` capture rules into the block
    header; default OFF."""
    s = _store(store_env)
    s.upsert_daily_note(contact_id=1, body_delta="today's note")

    from magi.db.settings import state_set as _state_set
    _state_set(str(store_env), "system.show_daily_note_prompt", "true")

    from magi.agent.system_prompt import build_system_prompt
    from magi.prompts import load_soul

    rendered = build_system_prompt(
        str(store_env), uid=1, soul=load_soul(),
    )
    assert "记录重点" in rendered
    assert "禁忌规则" in rendered


def test_system_prompt_omits_block_when_no_daily_row(store_env):
    """Fresh contact, no daily note yet — the block is silently
    empty so the assembly helper drops it."""
    from magi.agent.system_prompt import build_system_prompt
    from magi.prompts import load_soul

    rendered = build_system_prompt(
        str(store_env), uid=1, soul=load_soul(),
    )
    assert "今日 daily_note" not in rendered
