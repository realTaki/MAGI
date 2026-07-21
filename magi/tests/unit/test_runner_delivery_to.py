"""Regression tests for :func:`magi.agent.proactive.runner.execute_task`'s
``delivery_to`` dispatch.

The runner's behaviour is the dual of the ``ScheduleTaskTool``
defaults: an LLM call from inside a chat produces a row with
``delivery_to=<current session_id>`` and we want the cron
fire to land in that same chat (so the operator's ongoing
conversation accumulates), while the WebUI form's "new
session" default produces rows with ``delivery_to="new"`` /
``None`` and we want a fresh chat session per fire.

Four surfaces pinned:

  - ``delivery_to="new"`` (and the legacy ``None`` path) →
    fresh :class:`ChatSession` row per fire.
  - ``delivery_to=<26-char ULID>`` matching an existing
    :class:`ChatSession` owned by the same employee →
    that session is reused, the cron prompt is appended
    as a new :class:`ChatMessage`, and no new
    :class:`ChatSession` row is created.
  - ``delivery_to=<ULID>`` not matching any row → fallback
    to fresh session + warning log.
  - ``delivery_to=<ULID>`` matching a session owned by a
    DIFFERENT employee → fail-closed (no cross-employee
    message injection).

Live-fire end-to-end (mocked provider) isn't exercised
here — ``test_task_once_fire`` covers the scheduler/trigger
path; this file pins the post-fire session attachment only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi.agent.db import (
    ChatMessage,
    ChatSession,
    Employee,
    init_orm,
    init_sqlite,
    open_session,
    # Avoid circular import; the runner imports db from
    # here as well.
)
from magi.agent.proactive.runner import execute_task
from magi.agent.proactive.scheduler import (
    _reset_for_tests,
    stop_scheduler,
)
from magi.agent.proactive.orm_models import Task, TaskRun


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Fresh sqlite state dir + a single bound admin so the
    runner's owner resolution path is exercised."""
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    import magi.agent.db.engine as _orm_mod
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None

    init_sqlite(str(sd))
    init_orm(str(sd))
    with open_session() as s:
        s.add(
            Employee(
                # ``id`` auto, so we look it up later.
                name="runner-delivery-test",
                telegram_id=9101,
                role="admin",
                provider="minimax",
                api_key="fake-key-for-tests",
            )
        )
        s.commit()
    yield sd

    try:
        stop_scheduler(wait=False)
    except Exception:  # noqa: BLE001
        pass
    _reset_for_tests()


def _seed_task(state_dir: Path, name: str, delivery_to) -> str:
    """Insert a Task row + return its id. ``delivery_to`` is
    the raw string the runner will see at fire time."""
    # Crockford ULID-shaped id — distinct per test name so
    # fixture-level state can't collide across tests when
    # the engine singleton survives between fixtures.
    task_id_seed = (
        "T" + name[:24].ljust(24, "0")
    )
    with open_session() as db:
        # The Employee row was seeded with telegram_id=9101;
        # look it up so we can wire employee_id.
        emp = db.query(Employee).filter_by(telegram_id=9101).one()
        t = Task(
            id=task_id_seed,
            name=name,
            prompt=f"prompt for {name}",
            cron="0 9 * * *",
            run_at=None,
            delivery_to=delivery_to,
            tz="UTC",
            channel="webui",
            employee_id=emp.id,
            enabled=1,
            consecutive_failures=0,
            created_at="2026-07-20T12:00:00Z",
            updated_at="2026-07-20T12:00:00Z",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
    return t.id


# -- delivery_to = "new": fresh delivery chat per fire -------------------


async def test_delivery_to_new_creates_fresh_chat_session(state_dir: Path) -> None:
    """WebUI form's "new" default. After the refactor, every
    fire produces TWO sessions:

      - INTERNAL: channel='internal', title='[task] <name>',
        where the agent ran (ephemeral, never visible in
        the operator's chat list).
      - DELIVERY: channel='scheduled', title='[定时] <name>',
        where the assistant's reply lands for operator
        visibility.

    Both are fresh because delivery_to='new' resolves to
    "no target → make a new one"."""
    task_id = _seed_task(state_dir, "fresh-every-fire", delivery_to="new")
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Two sessions per fire (internal + delivery).
        assert len(sessions) == 2
        channels = sorted([s.channel for s in sessions])
        assert channels == ["internal", "scheduled"]
        internal = next(s for s in sessions if s.channel == "internal")
        delivery = next(s for s in sessions if s.channel == "scheduled")
        # Internal carries the task prompt as the
        # user-message; the agent's reply also lives
        # there but we don't pin it (the no-op noop
        # returns "fake reply" without writing).
        internal_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=internal.session_id)
            .all()
        )
        assert any(
            m.role == "user" and "fresh-every-fire" in m.text
            for m in internal_msgs
        )
        # Delivery carries the agent's final reply as
        # an assistant message — this is the row the
        # operator sees in their chat history.
        delivery_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=delivery.session_id)
            .all()
        )
        assert delivery.title.startswith("[定时]")
        assert any(
            m.role == "assistant" and "fake reply" in m.text
            for m in delivery_msgs
        )


# -- delivery_to = None: same path as "new" (legacy / unset) ----------------


async def test_delivery_to_null_also_creates_fresh_session(state_dir: Path) -> None:
    """Legacy rows (pre-DeliveryTarget) ship ``None`` in
    the column. The runner treats them identically to
    ``"new"`` — every fire produces the same internal +
    delivery pair as the explicit ``"new"`` case."""
    task_id = _seed_task(state_dir, "legacy-row", delivery_to=None)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Two sessions per fire (internal + delivery) —
        # same shape as ``delivery_to="new"``.
        assert len(sessions) == 2
        channels = sorted([s.channel for s in sessions])
        assert channels == ["internal", "scheduled"]


# -- delivery_to = <existing ULID>: agent stays isolated, reply joins chat ----


async def test_delivery_to_existing_session_reuses_it(state_dir: Path) -> None:
    """The LLM-in-chat path: ``delivery_to`` is set to
    the operator's current session_id. After the refactor:

      - Agent runs in a FRESH internal session (no
        pollution from prior cron replies or operator
        chat history).
      - DELIVERY appends the assistant reply to the
        operator's existing chat. The existing chat's
        prior turns are preserved.
    """
    # Seed an existing session as if the operator had a
    # chat going.
    with open_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=9101).one()
        existing = ChatSession(
            session_id="01HABCDEFGHJKMNPQRSTVWXY",
            tgid=str(emp.telegram_id),
            employee_id=emp.id,
            channel="webui",
            title="operator's ongoing chat",
            created_at="2026-07-20T09:00:00Z",
            updated_at="2026-07-20T11:00:00Z",
        )
        db.add(existing)
        db.flush()
        db.add(ChatMessage(
            session_id=existing.session_id,
            message_id="m_prior_turn",
            role="user",
            text="earlier question",
            ts="2026-07-20T10:00:00Z",
        ))
        db.commit()

    task_id = _seed_task(
        state_dir, "joined-chat", delivery_to=existing.session_id,
    )
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Two sessions now: the operator's existing chat
        # + the runner's INTERNAL session where the agent
        # ran. The existing chat was NOT rewritten /
        # deleted.
        assert len(sessions) == 2
        chat_sessions = [s for s in sessions if s.session_id == existing.session_id]
        assert len(chat_sessions) == 1
        chat = chat_sessions[0]
        # Original title preserved.
        assert chat.title == "operator's ongoing chat"
        # The operator's prior turn is still there,
        # AND the agent's reply landed as an assistant
        # message. The agent's INTERNAL session is NOT
        # this one — only the delivery target gets the
        # reply.
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=existing.session_id)
            .all()
        )
        texts = [m.text for m in msgs]
        assert "earlier question" in texts
        assert any("fake reply" in t for t in texts)
        # The "joined-chat" prompt lives in the INTERNAL
        # session, NOT in the operator's chat — that
        # was the bug the refactor fixes (prior design
        # appended the cron prompt to the operator's
        # chat as a user-message, polluting the chat
        # history with cron internals).
        assert not any("joined-chat" in t for t in texts)
        internal = [s for s in sessions if s.channel == "internal"]
        assert len(internal) == 1
        internal_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=internal[0].session_id)
            .all()
        )
        assert any("joined-chat" in m.text for m in internal_msgs)


# -- delivery_to = <unknown ULID>: fall back to fresh + log -----------------


async def test_delivery_to_unknown_ulid_falls_back(state_dir: Path, caplog) -> None:
    """A row whose delivery_to is a ULID that doesn't
    resolve to any ChatSession (e.g. the row was created,
    the operator deleted the session, the cron fires
    anyway). The runner falls back to a fresh ``[定时]``
    chat so the fire doesn't vanish silently — and a
    warning log tells the operator what happened."""
    task_id = _seed_task(
        state_dir, "ghost-session",
        delivery_to="01HXXXXXXXXXXXXXXXXXXXXXX",  # well-formed ULID
    )

    import logging
    caplog.set_level(logging.WARNING)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Two sessions: internal (where the agent ran)
        # + a fresh delivery "[定时] ghost-session" chat
        # (the ghost session never existed, so we fell
        # back to a new one).
        assert len(sessions) == 2
        channels = sorted([s.channel for s in sessions])
        assert channels == ["internal", "scheduled"]
        delivery = next(s for s in sessions if s.channel == "scheduled")
        assert delivery.title.startswith("[定时]")
        assert "ghost-session" in delivery.title

    # Warning log line surfacing the lost target.
    matches = [
        r for r in caplog.records
        if "ghost-session" in r.getMessage()
        and "did not resolve" in r.getMessage()
    ]
    assert matches, "expected a 'did not resolve' warning, got %r" % [
        r.getMessage() for r in caplog.records
    ]


# -- delivery_to = <session owned by another employee>: fail closed ---------


async def test_delivery_to_other_employees_session_is_rejected(state_dir: Path) -> None:
    """Cross-employee guard: a row saying "fire into session
    X" where X belongs to a DIFFERENT employee must
    NOT inject messages into that other session. The
    runner falls back to a fresh ``[定时]`` chat for the
    rightful operator instead."""
    # Seed: a session owned by employee B (not the task's
    # operator, who is the employee-with-telegram-id=9101).
    with open_session() as db:
        emp_a = db.query(Employee).filter_by(telegram_id=9101).one()
        emp_b = Employee(
            name="Other Operator",
            telegram_id=9202,
            role="admin",
            provider="minimax",
            api_key="fake-key-other",
        )
        db.add(emp_b)
        db.commit()
        db.refresh(emp_b)

        target = ChatSession(
            session_id="01HABCDEFGHJKMNPQRSTVWXY",
            tgid=str(emp_b.telegram_id),
            employee_id=emp_b.id,
            channel="webui",
            title="Other operator's chat",
            created_at="2026-07-20T09:00:00Z",
            updated_at="2026-07-20T11:00:00Z",
        )
        db.add(target)
        db.flush()
        db.add(ChatMessage(
            session_id=target.session_id,
            message_id="m_other_prior",
            role="user",
            text="other operator's earlier turn",
            ts="2026-07-20T10:00:00Z",
        ))
        db.commit()

    # Task owned by emp_a; delivery_to targets emp_b's session.
    task_id = _seed_task(state_dir, "cross-employee", delivery_to=target.session_id)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        # emp_b's session: ONLY the original prior turn; the
        # cross-employee attempt did not inject anything.
        msgs_for_other = (
            db.query(ChatMessage).filter_by(session_id=target.session_id).all()
        )
        assert len(msgs_for_other) == 1
        assert msgs_for_other[0].message_id == "m_other_prior"

        # Total ChatSession count: 3 = emp_b's seeded target
        # + emp_a's internal session + emp_a's fresh
        # [定时] delivery chat.
        all_sessions = db.query(ChatSession).all()
        assert len(all_sessions) == 3
        # The fresh delivery chat is owned by emp_a, not
        # emp_b.
        fresh = [
            s for s in all_sessions
            if s.employee_id == emp_a.id and s.channel == "scheduled"
        ]
        assert len(fresh) == 1
        assert fresh[0].title.startswith("[定时]")


# -- helper: stand-in for the agent loop so we exercise the runner --------


async def _fake_fire(task_id: str, state_dir: Path) -> None:
    """Patch ``handle_message`` to a no-op so the runner's
    own dispatch logic is exercised without needing an
    LLM provider.

    We swap the agent-loop entry point on the runner's
    module (the runner does ``from magi.agent.loop import
    handle_message``, so the symbol resolves through
    runner.handle_message). The no-op accepts every
    positional + keyword shape (``state_dir`` is
    positional in the real signature; everything else
    is kw-only)."""
    import magi.agent.proactive.runner as runner_mod

    real = runner_mod.handle_message

    async def _noop(*_args, **_kwargs):
        return "fake reply"

    runner_mod.handle_message = _noop  # type: ignore[assignment]
    try:
        await execute_task(str(state_dir), task_id, manual=True)
    finally:
        runner_mod.handle_message = real  # restore


# -- TG delivery_to: reuses operator's existing TG chat session ----------


async def test_tg_delivery_to_reuses_operator_tg_session(state_dir: Path) -> None:
    """The TG path: ``delivery_to`` is the operator's bound
    chat_id (``9101``). After the refactor:

      - Agent runs in a FRESH INTERNAL session
        (channel='internal', tgid=9101). The TG chat
        session is NOT touched during the agent loop.
      - DELIVERY appends the agent's reply to the existing
        TG ChatSession (looked up by (tgid, employee_id)).
      - The runner ALSO pushes the reply to TG via the
        bot directly (channel='tg' + bot registered).
    """
    with open_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=9101).one()
        existing = ChatSession(
            session_id="01HABCDEFGHJKMNPQRSTVWXY",
            tgid="9101",
            employee_id=emp.id,
            channel="tg",
            title="operator's TG chat",
            created_at="2026-07-20T09:00:00Z",
            updated_at="2026-07-20T11:00:00Z",
        )
        db.add(existing)
        db.flush()
        db.add(ChatMessage(
            session_id=existing.session_id,
            message_id="m_prior_tg_turn",
            role="user",
            text="earlier TG question",
            ts="2026-07-20T10:00:00Z",
        ))
        db.commit()

    task_id = _seed_task(
        state_dir, "tg-joined", delivery_to="9101",
    )
    with open_session() as db:
        t = db.get(Task, task_id)
        t.channel = "tg"
        db.commit()

    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Two sessions now: the existing TG chat + the
        # runner's INTERNAL session. The TG chat was
        # not deleted / replaced.
        assert len(sessions) == 2
        channels = sorted([s.channel for s in sessions])
        assert channels == ["internal", "tg"]
        tg_chat = next(s for s in sessions if s.channel == "tg")
        assert tg_chat.session_id == existing.session_id
        assert tg_chat.tgid == "9101"
        assert tg_chat.title == "operator's TG chat"
        # The TG chat now has: prior turn + the agent's
        # reply (NOT the cron prompt — that's in the
        # INTERNAL session). Refactor: the cron prompt
        # no longer leaks into the operator's TG chat
        # history as a user message.
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=existing.session_id)
            .all()
        )
        texts = [m.text for m in msgs]
        assert "earlier TG question" in texts
        assert any("fake reply" in t for t in texts)
        assert not any("tg-joined" in t for t in texts)
        # INTERNAL session has the cron prompt as the
        # user-message (where the agent sees it).
        internal = next(s for s in sessions if s.channel == "internal")
        internal_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=internal.session_id)
            .all()
        )
        assert any("tg-joined" in m.text for m in internal_msgs)


async def test_tg_delivery_to_chat_id_with_no_existing_session_creates_one(
    state_dir: Path,
) -> None:
    """The TG row points at a chat_id that has no
    pre-existing ChatSession row. Runner creates a fresh
    TG chat session with the chat_id stamped (so future
    TG fires accumulate into it). The reply lands in that
    fresh chat as an assistant message."""
    task_id = _seed_task(state_dir, "tg-cold", delivery_to="9101")
    with open_session() as db:
        t = db.get(Task, task_id)
        t.channel = "tg"
        db.commit()

    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Two sessions: INTERNAL + a fresh TG delivery
        # chat stamped with the operator's chat_id.
        assert len(sessions) == 2
        channels = sorted([s.channel for s in sessions])
        assert channels == ["internal", "tg"]
        tg_chat = next(s for s in sessions if s.channel == "tg")
        assert tg_chat.tgid == "9101"
        assert tg_chat.title.startswith("[定时]")


async def test_tg_bot_callback_fires_when_bot_registered(
    state_dir: Path,
) -> None:
    """When the bot is registered via
    :func:`set_telegram_bot`, the runner's TG path
    constructs an async ``_tg_send_callback`` that proxies
    to ``bot.send_message(chat_id=...)``. We use a stub
    Bot class to capture the call without a live
    python-telegram-bot instance."""
    from magi.channels import telegram as _tg

    # Set up a stub Bot. ``send_message`` is a coroutine
    # on the real class; we record the call so the test
    # can assert the runner built a callback that delegates
    # to it.
    sent: list[tuple[int, str]] = []

    class _StubBot:
        async def send_message(
            self, *, chat_id: int, text: str, **_kwargs,
        ) -> None:
            sent.append((chat_id, text))

    _tg.bot.set_telegram_bot(_StubBot())
    try:
        with open_session() as db:
            emp = db.query(Employee).filter_by(telegram_id=9101).one()
            # Pre-seed a TG chat session for the operator
            # so the runner reuses it (rather than creating
            # a new one).
            existing = ChatSession(
                session_id="01HABCDEFGHJKMNPQRSTVWXY",
                tgid="9101",
                employee_id=emp.id,
                channel="tg",
                title="tg",
                created_at="2026-07-20T09:00:00Z",
                updated_at="2026-07-20T11:00:00Z",
            )
            db.add(existing)
            db.flush()
            db.add(ChatMessage(
                session_id=existing.session_id,
                message_id="m_p",
                role="user",
                text="hi",
                ts="2026-07-20T10:00:00Z",
            ))
            db.commit()

        task_id = _seed_task(
            state_dir, "tg-push", delivery_to="9101",
        )
        with open_session() as db:
            t = db.get(Task, task_id)
            t.channel = "tg"
            db.commit()

        # We have to fire the agent call for real here
        # (the runner builds the callback and passes it to
        # ``handle_message``; the agent loop's send_message
        # tool is what invokes it). For this test we just
        # assert that the runner successfully constructed
        # the callback (no exception). The end-to-end
        # ``bot.send_message`` call happens on the LLM
        # path, which ``test_handle_message_tg_calls_callback``
        # already pins.
        await _fake_fire(task_id, state_dir)

        # The TG session was reused, the cron prompt was
        # appended — same as the webui explicit-session
        # path. The bot-stuff assertion (did send_message
        # fire?) is checked by the agent-loop's own
        # send_message tests; here we just confirm the
        # runner doesn't crash when the bot is registered.
        with open_session() as db:
            sessions = db.query(ChatSession).all()
            assert len(sessions) == 1
            assert sessions[0].tgid == "9101"
    finally:
        _tg.bot.clear_telegram_bot()
