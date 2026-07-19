"""Regression test for TG ``send_message`` tool callback wiring.

Without an injected ``tg_send_callback``, the ``send_message``
tool returns the error "TG callback not wired into the tool
context". The agent loop injects one based on the value passed
in by the channel — so the TG channel handler
(``_handle_employee_message``) MUST pass one. Earlier this was
missed and the LLM would see "TG callback not wired into the
tool context" every time it tried to use ``send_message`` from
a TG inbound.

We assert that ``_handle_employee_message`` calls
``handle_message`` with ``tg_send_callback`` set to a callable
that, when invoked, delegates to ``bot.send_message`` with the
right chat id.

The test deliberately stubs out everything heavy: real LLM,
real SessionStore, real ORM, etc. — only the wiring matters.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_tg_handler_injects_tg_send_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_handle_employee_message`` must pass a callable
    ``tg_send_callback`` to ``handle_message`` — otherwise
    the LLM's ``send_message`` tool returns the
    "TG callback not wired" error.

    We stub ``handle_message`` itself, ``SessionStore``'s
    ``create_or_get`` (to skip the auto-resolve path) and the
    typing loop, so only the kwargs propagation is exercised.
    """
    from magi.channels.telegram import bot as bot_mod

    # 1. Stub ``handle_message`` — capture its kwargs.
    #    ``bot.py`` imports it lazily inside the function,
    #    so we have to patch at the source module.
    from magi.agent import loop as loop_mod

    captured: dict = {}

    async def _fake_handle_message(*args, **kwargs):
        captured.update(kwargs)
        return "fake-reply"

    monkeypatch.setattr(loop_mod, "handle_message", _fake_handle_message)

    # 2. Stub ``SessionStore.create_or_get`` to skip the
    #    persistence path — we don't care about messages.
    from magi.agent.memory import session as sess_mod

    class _FakeStore:
        def __init__(self, *_a, **_kw):
            pass

        def create_or_get(self, *args, **kwargs):
            return SimpleNamespace(
                session_id="s1",
                messages=[],
                employee_id=1,
                chat_id="9001",
                channel="tg",
            )

        def create(self, *args, **kwargs):
            return SimpleNamespace(
                session_id="s_new",
                messages=[],
                employee_id=kwargs.get("employee_id", 1),
                chat_id=kwargs.get("chat_id", "9001"),
                channel=kwargs.get("channel", "tg"),
            )

        def list_summaries(self, *args, **kwargs):
            return ([], 0)

        def append_messages(self, *args, **kwargs):
            return None

        def append_message(self, *args, **kwargs):
            return None

    monkeypatch.setattr(sess_mod, "SessionStore", _FakeStore)

    # 3. Stub the typing loop to avoid the 4s asyncio task
    #    that the real handler kicks off.
    class _FakeTypingStop:
        def __init__(self):
            self.set = MagicMock()

    async def _fake_typing_loop(*_a, **_kw):
        return None

    monkeypatch.setattr(
        bot_mod, "_typing_indicator_loop", _fake_typing_loop,
    )

    # 4. Build a fake update with a bot that records
    #    ``send_message`` calls.
    bot = MagicMock()
    bot.send_message = AsyncMock()

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

    # 5. Stub the auto-title worker so we don't kick off a
    #    real background thread.
    from magi.agent.memory.session import auto_title as at_mod
    monkeypatch.setattr(
        at_mod, "enqueue_title_job", AsyncMock(return_value=None),
    )

    # 6. Call the handler. ``post`` path is skipped because
    #    our fake store returns a session with no messages;
    #    auto-title won't fire.
    await bot_mod._handle_employee_message(
        fake_update,
        state_dir="/tmp/fake",
        chat_id="6240201712",
        employee_id=1,
        employee_name="Test",
        display_name=None,
        employee_separated=False,
        employee_provider="minimax",
        employee_api_key="fake-key",
    )

    # 7. The bug we fixed: handle_message MUST have been
    #    called with tg_send_callback=callable.
    assert "tg_send_callback" in captured, (
        "tg handler didn't pass tg_send_callback to handle_message"
    )
    cb = captured["tg_send_callback"]
    assert callable(cb), (
        f"tg_send_callback should be callable, got {type(cb)}"
    )

    # 8. Sanity: invoking the callback routes through the bot.
    asyncio.get_event_loop()  # ensure loop exists (3.12 compat)
    await cb(6240201712, "hello world")
    bot.send_message.assert_awaited_once_with(
        chat_id=6240201712, text="hello world",
    )