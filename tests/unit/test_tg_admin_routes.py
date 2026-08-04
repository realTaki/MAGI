"""Tests for the TG inbound dispatch table.

Pins the role-based routing in :func:`_on_message`. The
admin branch is the most regression-prone — v0 originally
short-circuited admins to ``logger.info(... return)`` so
they wouldn't burn their API key on TG chitchat. Once D.4
required per-contact credentials anyway, and D.10/D.11
made TG chat-with-EVA a real affordance, admins needed the
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
    
    from magi.bus.db import init_sqlite
    from magi.bus.models.local.contact import Contact
    from magi.bus.db import (
        init_orm,
        open_session,
    )

    init_sqlite(str(state))
    init_orm(str(state))
    return state

def _seed_contact(state_dir: str, *, delivery_address: int, role: str, admin: bool = False):
    from magi.bus.models.local.contact import Contact
    from magi.bus.db import open_session

    with open_session() as s:
        s.query(Contact).delete()
        s.add(
            Contact(
                name=f"TA-{role}",
                telegram_id=delivery_address,
                role=role,
                admin=admin
            )
        )
        s.commit()

def _make_update(*, delivery_address: int, message_id: int, text: str):
    """Build a minimal ``Update``-shaped mock.

    We don't use the real ``telegram.Update`` because the
    intent here is to verify *routing*, not the TG SDK.
    A MagicMock with the attributes the handler reads
    (``effective_chat.id``, ``effective_message.message_id``
    / ``.text``, ``effective_message.reply_text``,
    ``get_bot()``) is enough.
    """
    update = MagicMock()
    update.effective_chat.id = delivery_address
    update.effective_message.message_id = message_id
    update.effective_message.text = text
    update.effective_message.reply_text = AsyncMock()
    update.get_bot = MagicMock(return_value=MagicMock(
        set_message_reaction=AsyncMock(return_value=True)))
    return update

@pytest.mark.asyncio
async def test_admin_message_reaches_handler(tg_admin_env, monkeypatch):
    """D.11 fix: ``admin`` role messages are NOT short-
    circuited. They must reach ``_handle_contact_message``,
    which means a durable agent message is published and
    ``set_message_reaction`` is called (the read-receipt).
    """
    _seed_contact(str(tg_admin_env), delivery_address=9001, admin=True, role="assigned")

    from magi.channels.telegram.bot import _on_message
    update = _make_update(message_id=42, delivery_address=9001, text="在吗")

    from magi.agent import worker as worker_mod
    published = AsyncMock(return_value="run-admin")
    monkeypatch.setattr(worker_mod, "submit_agent_message", published)

    await _on_message(update, MagicMock())

    # The handler ran — it published work and returned without waiting for
    # inference. DeliveryWorker sends the eventual reply.
    published.assert_awaited_once()
    # And the read-reaction was set on the inbound message.
    bot = update.get_bot.return_value
    bot.set_message_reaction.assert_awaited()

@pytest.mark.asyncio
async def test_assigned_message_reaches_handler(tg_admin_env, monkeypatch):
    """``assigned`` role is the historical happy path;
    pinned here so the admin fix doesn't regress it."""
    _seed_contact(str(tg_admin_env), delivery_address=9001, role="assigned")

    from magi.channels.telegram.bot import _on_message
    update = _make_update(message_id=43, delivery_address=9001, text="hello")

    from magi.agent import worker as worker_mod
    published = AsyncMock(return_value="run-assigned")
    monkeypatch.setattr(worker_mod, "submit_agent_message", published)

    await _on_message(update, MagicMock())

    published.assert_awaited_once()
    update.get_bot.return_value.set_message_reaction.assert_awaited()

@pytest.mark.asyncio
async def test_contact_role_is_refused(tg_admin_env):
    """``contact`` / ``guest`` stay refused — they're
    not served by this MAGI. The admin fix must not
    have widened the gate to include them."""
    _seed_contact(str(tg_admin_env), delivery_address=9001, role='guest')

    from magi.channels.telegram.bot import _on_message
    update = _make_update(message_id=44, delivery_address=9001, text="hi")

    await _on_message(update, MagicMock())

    # Cross-company refusal reply goes out, but no LLM call
    # was issued (no reaction either — the rejection path
    # doesn't react).
    update.effective_message.reply_text.assert_awaited()
    update.get_bot.return_value.set_message_reaction.assert_not_awaited()
