"""Regression test for TG inbound → ``send_message`` tool wiring.

D.28 refactor removed the per-call ``tg_send_callback``
parameter from ``handle_message``. The ``send_message`` tool now
routes through :func:`magi.channels.dispatcher.send_to_session`,
which uses the live TG bot instance registered at
``save-bot`` time. This test pins the post-refactor contract:

  - The TG inbound handler (``_handle_contact_message``) hands
    off to ``handle_message`` with the channel + delivery
    address that the dispatcher needs to route a future
    ``send_message`` push back to the right chat id.
  - The TG bot is reachable from the same process as the
    ``send_message`` tool (no cross-thread event-loop dance),
    so a tool call dispatched from a TG inbound lands on the
    same bot that received the inbound.

The test:
  1. Stubs ``handle_message`` itself to capture kwargs.
  2. Stubs the typing indicator loop (so we don't kick off a
     real 4-second background task).
  3. Stubs ``enqueue_title_job`` so we don't spawn the worker.
  4. Calls the real ``_handle_contact_message`` end-to-end
     (so we exercise the actual wiring).

Everything else (real ``SessionStore``, real ORM, real state
dir) is genuine — we only intercept the boundaries that would
otherwise require an LLM or a live TG connection.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from magi.channels import Channel


@pytest.fixture
def tg_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Real state + workspace dirs, real ORM + sqlite, one
    contact seeded. The fake ``handle_message`` we install
    below shortcuts the LLM call so the rest of the path
    can run end-to-end without external services."""
    state = tmp_path / "state"
    state.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.db.engine as orm_mod

    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.db import Contact, init_orm, init_sqlite, open_session

    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as s:
        contact = Contact(id=1, name="Taki", telegram_id=6240201712, admin=True, role="assigned")
        s.add(contact)
        s.commit()
        s.refresh(contact)

    return state


@pytest.mark.asyncio
async def test_tg_handler_injects_tg_send_callback(
    monkeypatch: pytest.MonkeyPatch, tg_state_dir
) -> None:
    """``_handle_contact_message`` must pass a callable
    ``tg_send_callback`` to ``handle_message`` — otherwise
    the LLM's ``send_message`` tool returns the
    "TG callback not wired" error.
    """
    from magi.channels.telegram import bot as bot_mod
    from magi.agent import step as step_mod
    from magi.agent.memory.session import auto_title as at_mod

    # 1. Stub ``handle_message`` — capture kwargs.
    captured: dict = {}

    async def _fake_step(*args, **kwargs):
        captured.update(kwargs)
        return step_mod.AgentStepResult(
            text="fake-reply",
            tool_uses=(),
            assistant_blocks=(),
            provider="test",
            model="test",
            usage={},
            messages=(),
        )

    monkeypatch.setattr(step_mod, "run_agent_step", _fake_step)

    # 2. Stub the typing loop (real one creates a 4s task).
    async def _fake_typing_loop(*_a, **_kw):
        return None

    monkeypatch.setattr(bot_mod, "_typing_indicator_loop", _fake_typing_loop)

    # 3. Stub the auto-title enqueue (real one spawns a worker).
    monkeypatch.setattr(at_mod, "enqueue_title_job", AsyncMock(return_value=None))

    # 4. Build a fake update + bot.
    bot = MagicMock()
    bot.send_message = AsyncMock()
    # ``set_message_reaction`` is called for the read-emoji on
    # inbound; stub it so the test doesn't try to hit the TG API.
    bot.set_message_reaction = AsyncMock(return_value=None)

    fake_update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=6240201712),
        effective_message=SimpleNamespace(text="hi", message_id=1, reply_text=AsyncMock()),
        message=SimpleNamespace(text="hi"),
        get_bot=lambda: bot,
    )

    # 5. Call the real handler.
    await bot_mod._handle_contact_message(
        fake_update,
        state_dir=str(tg_state_dir),
        delivery_address="6240201712",  # same as the seeded Contact's telegram_id
        uid=1,
        contact_name="Taki",
        display_name=None,
        contact_separated=False,
        contact_role="assigned",
    )

    # 6. Post-D.28 contract: ``handle_message`` is called with
    #    the channel + delivery_address that the dispatcher
    #    needs to route a future ``send_message`` tool call
    #    back to the right TG chat. No per-call callback.
    assert captured["channel"] == Channel.TG
    assert "tg_send_callback" not in captured, (
        "tg_send_callback was removed in D.28 — the dispatcher "
        "uses the live bot instance registered at save-bot "
        "time, so the tool context no longer needs a callback."
    )
