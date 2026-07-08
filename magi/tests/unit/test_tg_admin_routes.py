"""Tests for the TG inbound dispatch table.

Pins the role-based routing in :func:`_on_message`. The
admin branch is the most regression-prone — v0 originally
short-circuited admins to ``logger.info(... return)`` so
they wouldn't burn their API key on TG chitchat. Once D.4
required per-employee credentials anyway, and D.10/D.11
made TG chat-with-EVE a real affordance, admins needed the
full handler path so their TG messages actually got a
reply + emoji reaction.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def tg_admin_env(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    from magi.agent.state import init_sqlite
    from magi.agent.state.orm import Employee, init_orm, open_session

    init_sqlite(str(state))
    init_orm(str(state))
    return state


def _seed_employee(state_dir: str, *, chat_id: int, role: str):
    from magi.agent.state.orm import Employee, open_session

    with open_session() as s:
        s.query(Employee).delete()
        s.add(
            Employee(
                name=f"TA-{role}",
                telegram_id=chat_id,
                role=role,
                provider="minimax",
                api_key="fake-key",
            )
        )
        s.commit()


def _make_update(*, chat_id: int, message_id: int, text: str):
    """Build a minimal ``Update``-shaped mock.

    We don't use the real ``telegram.Update`` because the
    intent here is to verify *routing*, not the TG SDK.
    A MagicMock with the attributes the handler reads
    (``effective_chat.id``, ``effective_message.message_id``
    / ``.text``, ``effective_message.reply_text``,
    ``get_bot()``) is enough.
    """
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message.message_id = message_id
    update.effective_message.text = text
    update.effective_message.reply_text = AsyncMock()
    update.get_bot = MagicMock(return_value=MagicMock(
        set_message_reaction=AsyncMock(return_value=True),
    ))
    return update


@pytest.mark.asyncio
async def test_admin_message_reaches_handler(tg_admin_env):
    """D.11 fix: ``admin`` role messages are NOT short-
    circuited. They must reach ``_handle_employee_message``,
    which means ``reply_text`` is called (the handler's
    success path) and ``set_message_reaction`` is called
    (the read-receipt).
    """
    _seed_employee(str(tg_admin_env), chat_id=6240201712, role="admin")

    from magi.channels.telegram.bot import _on_message
    update = _make_update(chat_id=6240201712, message_id=42, text="在吗")

    # Run the dispatcher; it will block on the LLM call.
    # We don't want the real provider — mock the agent loop
    # by short-circuiting ``handle_message`` to return a
    # canned string. (Patching at the import location is
    # needed because ``bot.py`` does
    # ``from magi.agent.loop import handle_message``
    # inside the handler.)
    import magi.agent.loop as agent_mod
    agent_mod.handle_message = AsyncMock(return_value="hi back")

    await _on_message(update, MagicMock())

    # The handler ran — a reply was sent.
    update.effective_message.reply_text.assert_awaited()
    # And the read-reaction was set on the inbound message.
    bot = update.get_bot.return_value
    bot.set_message_reaction.assert_awaited()


@pytest.mark.asyncio
async def test_assigned_message_reaches_handler(tg_admin_env):
    """``assigned`` role is the historical happy path;
    pinned here so the admin fix doesn't regress it."""
    _seed_employee(str(tg_admin_env), chat_id=9876543210, role="assigned")

    from magi.channels.telegram.bot import _on_message
    update = _make_update(chat_id=9876543210, message_id=43, text="hello")

    import magi.agent.loop as agent_mod
    agent_mod.handle_message = AsyncMock(return_value="hi back")

    await _on_message(update, MagicMock())

    update.effective_message.reply_text.assert_awaited()
    update.get_bot.return_value.set_message_reaction.assert_awaited()


@pytest.mark.asyncio
async def test_employee_role_is_refused(tg_admin_env):
    """``employee`` / ``guest`` stay refused — they're
    not served by this MAGI. The admin fix must not
    have widened the gate to include them."""
    _seed_employee(str(tg_admin_env), chat_id=1111, role="employee")

    from magi.channels.telegram.bot import _on_message
    update = _make_update(chat_id=1111, message_id=44, text="hi")

    await _on_message(update, MagicMock())

    # Cross-company refusal reply goes out, but no LLM call
    # was issued (no reaction either — the rejection path
    # doesn't react).
    update.effective_message.reply_text.assert_awaited()
    update.get_bot.return_value.set_message_reaction.assert_not_awaited()