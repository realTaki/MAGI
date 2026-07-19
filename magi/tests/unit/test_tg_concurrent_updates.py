"""Regression test for the TG ``concurrent_updates=True`` setting.

D.21's interrupt poll (``_drain_pending_user_messages``) only
sees follow-up messages that the bot has **already
persisted** to the session store. python-telegram-bot's
``Application`` defaults to ``concurrent_updates=False``,
which serialises per-chat updates at the dispatcher level —
so a follow-up TG message that arrives mid-tool-chain sits
in the bot's queue until the prior turn's handler fully
returns, and the user sees "two separate batches of
send_message calls" instead of the new message being
spliced into the live loop.

This test asserts ``start_bot`` constructs the ``Application``
with ``concurrent_updates=True`` so the dispatcher doesn't
gate inbound messages on a still-running handler.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest


@pytest.fixture
def _saved_token(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Pretend onboarding completed: a bot token saved into
    the state dir's ``settings`` table."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    from magi.agent.db import init_sqlite
    init_sqlite(str(state))

    from magi.agent.db.settings import state_set
    state_set(str(state), "telegram.bot_token", "fake:token")
    state_set(str(state), "telegram.bot_username", "fakebot")
    return state


def test_start_bot_enables_concurrent_updates(_saved_token) -> None:
    """``start_bot`` must pass ``concurrent_updates=True``
    to ``Application.builder().token(...).build()``."""
    from magi.channels.telegram import bot as bot_mod

    # We intercept ``Application.builder()`` to capture the
    # fluent configuration chain. The builder method calls
    # ``.concurrent_updates(True)`` exactly once on the
    # happy path; we record whether it was called and the
    # flag value.
    captured: dict = {"concurrent_updates": None}

    class _FakeBuilder:
        def token(self, t):
            captured["token"] = t
            return self

        def concurrent_updates(self, flag):
            captured["concurrent_updates"] = flag
            return self

        def build(self):
            return _FakeApplication()

    class _FakeApplication:
        def add_handler(self, *_a, **_kw):
            return None

        async def initialize(self):
            return None

        async def start(self):
            return None

        # ``updater`` is a property in the real Application.
        class _Updater:
            async def start_polling(self):
                return None

        updater = _Updater()

    with patch.object(bot_mod, "Application") as FakeApplication:
        FakeApplication.builder.return_value = _FakeBuilder()
        # ``start_bot`` runs the bot in a daemon thread that
        # parks on an Event that never gets set — it would
        # hang pytest forever if we let it run. Patch
        # ``threading.Thread.start`` to a no-op so we never
        # actually enter the run loop.
        with patch.object(
            threading.Thread, "start", lambda self: None,
        ):
            bot_mod.start_bot(str(_saved_token))

    assert captured["concurrent_updates"] is True, (
        "TG bot built Application without "
        "concurrent_updates=True; follow-up messages will "
        "be queued behind the in-flight handler and the D.21 "
        "interrupt poll will never see them."
    )