"""Regression test for TG ``send_message`` tool callback wiring.

Without an injected ``tg_send_callback``, the ``send_message``
tool returns the error "TG callback not wired into the tool
context". The agent loop injects one based on the value passed
in by the channel — so the TG channel handler
(``_handle_employee_message``) MUST pass one. Earlier this was
missed and the LLM would see "TG callback not wired into the
tool context" every time it tried to use ``send_message`` from
a TG inbound.

The test:
  1. Stubs ``handle_message`` itself to capture kwargs.
  2. Stubs the typing indicator loop (so we don't kick off a
     real 4-second background task).
  3. Stubs ``enqueue_title_job`` so we don't spawn the worker.
  4. Calls the real ``_handle_employee_message`` end-to-end
     (so we exercise the actual wiring).

Everything else (real ``SessionStore``, real ORM, real state
dir) is genuine — we only intercept the boundaries that would
otherwise require an LLM or a live TG connection.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def tg_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Real state + workspace dirs, real ORM + sqlite, one
    employee seeded. The fake ``handle_message`` we install
    below shortcuts the LLM call so the rest of the path
    can run end-to-end without external services."""
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

    with open_session() as s:
        emp = Employee(
            id=1,
            name="Taki",
            telegram_id=6240201712,
            role="admin",
            provider="minimax",
            api_key="fake-key-for-tests",
        )
        s.add(emp)
        s.commit()
        s.refresh(emp)

    return state


@pytest.mark.asyncio
async def test_tg_handler_injects_tg_send_callback(
    monkeypatch: pytest.MonkeyPatch, tg_state_dir,
) -> None:
    """``_handle_employee_message`` must pass a callable
    ``tg_send_callback`` to ``handle_message`` — otherwise
    the LLM's ``send_message`` tool returns the
    "TG callback not wired" error.
    """
    from magi.channels.telegram import bot as bot_mod
    from magi.agent import loop as loop_mod
    from magi.agent.memory.session import auto_title as at_mod

    # 1. Stub ``handle_message`` — capture kwargs.
    captured: dict = {}

    async def _fake_handle_message(*args, **kwargs):
        captured.update(kwargs)
        return "fake-reply"

    monkeypatch.setattr(loop_mod, "handle_message", _fake_handle_message)

    # 2. Stub the typing loop (real one creates a 4s task).
    async def _fake_typing_loop(*_a, **_kw):
        return None

    monkeypatch.setattr(
        bot_mod, "_typing_indicator_loop", _fake_typing_loop,
    )

    # 3. Stub the auto-title enqueue (real one spawns a worker).
    monkeypatch.setattr(
        at_mod, "enqueue_title_job", AsyncMock(return_value=None),
    )

    # 4. Build a fake update + bot.
    bot = MagicMock()
    bot.send_message = AsyncMock()
    # ``set_message_reaction`` is called for the read-emoji on
    # inbound; stub it so the test doesn't try to hit the TG API.
    bot.set_message_reaction = AsyncMock(return_value=None)

    fake_update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=6240201712),
        effective_message=SimpleNamespace(
            text="hi",
            message_id=1,
            reply_text=AsyncMock(),
        ),
        message=SimpleNamespace(text="hi"),
        get_bot=lambda: bot,
    )

    # 5. Call the real handler.
    await bot_mod._handle_employee_message(
        fake_update,
        state_dir=str(tg_state_dir),
        tgid="6240201712",  # same as the seeded Employee's telegram_id
        uid=1,
        employee_name="Taki",
        display_name=None,
        employee_separated=False,
        # Required since the call-site enum that runs
        # the handler adopted ``employee_role`` to thread
        # the TG user's role through to
        # ``handle_message(caller_role=...)``. The fake
        # bind in this test is always admin (the seeded
        # Employee in the chat handler's earlier branch).
        employee_role="admin",
        employee_provider="minimax",
        employee_api_key="fake-key-for-tests",
    )

    # 6. The fix: ``handle_message`` must have been called
    #    with a callable ``tg_send_callback``.
    assert "tg_send_callback" in captured, (
        "tg handler didn't pass tg_send_callback to handle_message — "
        "send_message tool will fail with 'TG callback not wired'."
    )
    cb = captured["tg_send_callback"]
    assert callable(cb), (
        f"tg_send_callback should be callable, got {type(cb)!r}"
    )

    # 7. Invoking the callback routes through bot.send_message
    #    with the tgid we passed in.
    await cb(6240201712, "hello from the LLM tool")
    bot.send_message.assert_awaited_once_with(
        tgid=6240201712,
        text="hello from the LLM tool",
    )