"""LLM-tool-level smoke for ``update_daily_note``.

Same shape as :mod:`test_action_item_tools`: seed an
assigned contact, run the tool, assert the row lands in the
``contact_notes`` table with ``kind='daily'``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


def _ctx(state_dir: str, *, uid: int = 1, role: str = "assigned", admin: bool = True):
    """Build a minimal ToolContext-shaped namespace."""
    return SimpleNamespace(
        state_dir=state_dir,
        workspace="/tmp/whatever",
        uid=uid,
        channel="webui",
        session_id="01ABCDEFGHJKMNPQRSTVWXYZAB",
        caller_role=role,
        admin=admin,
    )


@pytest.fixture
def state_dir(monkeypatch, tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import (
        Contact, init_orm, init_sqlite, open_session,
    )
    init_sqlite(str(sd))
    init_orm(str(sd))

    with open_session() as db:
        db.add(Contact(
            name="TA-daily-tool",
            telegram_id=91002,
            admin=True,
            role="assigned",
        ))
        db.commit()
    return sd


@pytest.mark.asyncio
async def test_update_daily_note_inserts_first_row(state_dir):
    from magi.agent.memory.contacts.tools import UpdateDailyNoteTool
    from magi.agent.db import open_session
    from magi.agent.db.models_contact import ContactNote

    tool = UpdateDailyNoteTool()
    ctx = _ctx(str(state_dir))
    res = await tool.run(ctx, body_delta="sent the Q3 invoice")
    assert res.is_error is False

    with open_session() as db:
        rows = db.query(ContactNote).filter_by(
            contact_id=1, kind="daily",
        ).all()
    assert len(rows) == 1
    assert rows[0].note == "sent the Q3 invoice"


@pytest.mark.asyncio
async def test_update_daily_note_appends_second_call(state_dir):
    from magi.agent.memory.contacts.tools import UpdateDailyNoteTool
    from magi.agent.db import open_session
    from magi.agent.db.models_contact import ContactNote

    tool = UpdateDailyNoteTool()
    ctx = _ctx(str(state_dir))
    await tool.run(ctx, body_delta="first delta")
    await tool.run(ctx, body_delta="second delta")

    with open_session() as db:
        rows = db.query(ContactNote).filter_by(
            contact_id=1, kind="daily",
        ).all()
    assert len(rows) == 1
    assert rows[0].note == "first delta\nsecond delta"


@pytest.mark.asyncio
async def test_update_daily_note_rejects_empty_body(state_dir):
    from magi.agent.memory.contacts.tools import UpdateDailyNoteTool

    tool = UpdateDailyNoteTool()
    res = await tool.run(_ctx(str(state_dir)), body_delta="   ")
    assert res.is_error is True
    assert "body_delta is required" in res.content


@pytest.mark.asyncio
async def test_update_daily_note_rejects_bad_date_format(state_dir):
    from magi.agent.memory.contacts.tools import UpdateDailyNoteTool

    tool = UpdateDailyNoteTool()
    res = await tool.run(
        _ctx(str(state_dir)),
        body_delta="x",
        note_date="not-a-date",
    )
    assert res.is_error is True
    assert "YYYY-MM-DD" in res.content


@pytest.mark.asyncio
async def test_update_daily_note_rejects_for_unassigned_contact(state_dir):
    """The in-run ``_gate`` blocks ``role='contact'`` (no admin) —
    same shape as the permanent-note tools."""
    from magi.agent.memory.contacts.tools import UpdateDailyNoteTool

    tool = UpdateDailyNoteTool()
    res = await tool.run(
        _ctx(str(state_dir), uid=2, role="contact", admin=False),
        body_delta="x",
    )
    assert res.is_error is True
    assert "role 'contact'" in res.content or "not permitted" in res.content.lower()


@pytest.mark.asyncio
async def test_update_daily_note_admits_admin_with_role_contact(state_dir):
    """``admin=True`` overrides the role enum — a colleague
    operator (``role='contact', admin=True``) can still write
    their own daily notes."""
    from magi.agent.memory.contacts.tools import UpdateDailyNoteTool
    from magi.agent.db import open_session
    from magi.agent.db.models_contact import ContactNote

    # Need a contact row at uid=2 with role='contact' so the FK resolves.
    from magi.agent.db import Contact, open_session as _open
    with _open() as db:
        db.add(Contact(
            id=2, name="TA-colleague",
            telegram_id=91003,
            admin=True, role="contact",
        ))
        db.commit()

    tool = UpdateDailyNoteTool()
    res = await tool.run(
        _ctx(str(state_dir), uid=2, role="contact", admin=True),
        body_delta="colleague note",
    )
    assert res.is_error is False
    with open_session() as db:
        rows = db.query(ContactNote).filter_by(
            contact_id=2, kind="daily",
        ).all()
    assert len(rows) == 1
    assert rows[0].note == "colleague note"