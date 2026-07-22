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


def _seed_task(
    state_dir: Path,
    name: str,
    *,
    channel: str = "webui",
    delivery_to: str | None = None,
    with_session: bool = True,
) -> tuple[str, str | None]:
    """Insert a Task row + return ``(task_id, session_id)``.

    Mirrors what the API + schedule_task tool do: at
    task-creation time, allocate a fresh
    ``channel="task"`` ``ChatSession`` and stamp
    ``task.session_id`` with it. Tests that simulate
    legacy rows (pre-session_id column) can pass
    ``with_session=False`` to skip the allocation —
    the runner's legacy-row fallback will backfill
    on first fire.
    """
    task_id_seed = (
        "T" + name[:24].ljust(24, "0")
    )
    with open_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=9101).one()
        session_id: str | None = None
        if with_session:
            session_id = new_session_id()
            db.add(ChatSession(
                session_id=session_id,
                tgid=str(emp.telegram_id or ""),
                employee_id=emp.id,
                channel="task",
                title=f"[定时] {name}",
                created_at="2026-07-20T12:00:00Z",
                updated_at="2026-07-20T12:00:00Z",
            ))
            db.flush()
        t = Task(
            id=task_id_seed,
            name=name,
            prompt=f"prompt for {name}",
            cron="0 9 * * *",
            run_at=None,
            delivery_to=delivery_to,
            session_id=session_id,
            tz="UTC",
            channel=channel,
            employee_id=emp.id,
            enabled=1,
            consecutive_failures=0,
            created_at="2026-07-20T12:00:00Z",
            updated_at="2026-07-20T12:00:00Z",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
    return t.id, session_id


# -- delivery_to = "new": fresh chat per fire -------------------


async def test_delivery_to_new_creates_fresh_chat_session(state_dir: Path) -> None:
    """WebUI form's "new" default. After the single-session
    refactor, every fire produces ONE ChatSession
    (``channel="task"``) that doubles as both the agent's
    working context AND the operator-visible record.

    Two tasks with ``delivery_to="new"`` produce two
    separate sessions — cross-task pollution is
    impossible by construction (each fire gets its own
    ULID).
    """
    task_id = _seed_task(state_dir, "fresh-every-fire", delivery_to="new")
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # Exactly one session per fire — the agent's
        # working context IS the operator-visible record.
        assert len(sessions) == 1
        sess = sessions[0]
        assert sess.channel == "task"
        assert sess.title.startswith("[定时]")
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=sess.session_id)
            .all()
        )
        # The user-message carries the task prompt; the
        # no-op fake reply doesn't write a row (only
        # the live agent loop would).
        assert any(
            m.role == "user" and "fresh-every-fire" in m.text
            for m in msgs
        )


# -- delivery_to = None: same path as "new" (legacy / unset) ----------------


async def test_delivery_to_null_also_creates_fresh_session(state_dir: Path) -> None:
    """Legacy rows (pre-DeliveryTarget) ship ``None`` in
    the column. The runner treats them identically to
    ``"new"`` — every fire produces one fresh session."""
    task_id = _seed_task(state_dir, "legacy-row", delivery_to=None)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        assert len(sessions) == 1
        assert sessions[0].channel == "task"


# -- delivery_to = <existing ULID>: reuse chat -----------------------------


async def test_delivery_to_existing_session_reuses_it(state_dir: Path) -> None:
    """The LLM-in-chat path: ``delivery_to`` is set to
    the operator's current session_id. The agent runs
    in that session, the operator's prior turns stay
    intact, and the agent's reply lands in the same
    chat. v0 keeps this branch for the "join my chat"
    semantic; two tasks sharing a chat will share the
    agent's context by design."""
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
        # Still exactly one session — the operator's chat.
        assert len(sessions) == 1
        assert sessions[0].session_id == existing.session_id
        assert sessions[0].title == "operator's ongoing chat"
        # Original title preserved.
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=existing.session_id)
            .all()
        )
        texts = [m.text for m in msgs]
        assert "earlier question" in texts
        # The agent's reply also landed here (the agent
        # ran in this session; the no-op fake reply
        # doesn't write, so we only assert the user-
        # message carrying the cron prompt is there).
        assert any("joined-chat" in t for t in texts)


# -- delivery_to = <unknown ULID>: fall back to fresh + log -----------------


async def test_delivery_to_unknown_ulid_falls_back(state_dir: Path, caplog) -> None:
    """A row whose delivery_to is a ULID that doesn't
    resolve to any ChatSession (e.g. the row was created,
    the operator deleted the session, the cron fires
    anyway). The runner falls back to a fresh session so
    the fire doesn't vanish silently — and a warning log
    tells the operator what happened."""
    task_id = _seed_task(
        state_dir, "ghost-session",
        delivery_to="01HXXXXXXXXXXXXXXXXXXXXXX",
    )

    import logging
    caplog.set_level(logging.WARNING)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # One fresh row produced — not zero (silent drop),
        # not zero-from-found (the ghost session never
        # existed).
        assert len(sessions) == 1
        assert sessions[0].title.startswith("[定时]")

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

    task_id = _seed_task(state_dir, "cross-employee", delivery_to=target.session_id)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        msgs_for_other = (
            db.query(ChatMessage).filter_by(session_id=target.session_id).all()
        )
        assert len(msgs_for_other) == 1
        assert msgs_for_other[0].message_id == "m_other_prior"

        all_sessions = db.query(ChatSession).all()
        # 1 emp_b's target + 1 emp_a's fresh fallback
        assert len(all_sessions) == 2
        fresh = [s for s in all_sessions if s.employee_id == emp_a.id]
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
    chat_id (``9101``). After the single-session refactor:

      - Agent runs in the existing TG ``ChatSession``
        (looked up by ``(tgid, employee_id, channel="tg")``).
        No new session is created — the operator's TG
        chat history is preserved.
      - The cron prompt is appended as a user-message
        (so the agent sees it in context).
      - The agent's ``send_message`` tool (wired by the
        runner) is responsible for the TG wire push.
        We don't assert on TG push here — that's the
        agent's call. The runner's job is just to
        wire the callback and reuse the session.
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
        # Still exactly one TG session — the existing one.
        assert len(sessions) == 1
        assert sessions[0].session_id == existing.session_id
        assert sessions[0].tgid == "9101"
        assert sessions[0].title == "operator's TG chat"
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=existing.session_id)
            .all()
        )
        texts = [m.text for m in msgs]
        assert "earlier TG question" in texts
        assert any("tg-joined" in t for t in texts)


async def test_tg_delivery_to_chat_id_with_no_existing_session_creates_one(
    state_dir: Path,
) -> None:
    """The TG row points at a chat_id that has no
    pre-existing ChatSession row. Runner creates a fresh
    TG chat session with that chat_id stamped, so the
    agent runs in a TG context bound to that address."""
    task_id = _seed_task(state_dir, "tg-cold", delivery_to="9101")
    with open_session() as db:
        t = db.get(Task, task_id)
        t.channel = "tg"
        db.commit()

    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # One fresh TG chat session, stamped with the
        # operator's chat_id.
        assert len(sessions) == 1
        assert sessions[0].channel == "tg"
        assert sessions[0].tgid == "9101"
        assert sessions[0].title.startswith("[定时]")


async def test_tg_multiple_fires_share_one_session(
    state_dir: Path,
) -> None:
    """Two fires of TG tasks against the same chat_id
    accumulate into ONE TG chat session — not one per
    fire. The whole point of ``channel="tg"`` lookup
    is conversation continuity: the operator's TG
    chat with the bot shows every cron reply as one
    thread. If the runner created a fresh session per
    fire, the operator's TG history would be fragmented
    into N short "[定时] X" rows."""
    # First fire: cold — creates the TG chat session.
    task_id_1 = _seed_task(state_dir, "first-fire", delivery_to="9101")
    with open_session() as db:
        t = db.get(Task, task_id_1)
        t.channel = "tg"
        db.commit()
    await _fake_fire(task_id_1, state_dir)

    # Second fire: same chat_id — should reuse the same
    # TG chat session.
    task_id_2 = _seed_task(state_dir, "second-fire", delivery_to="9101")
    with open_session() as db:
        t = db.get(Task, task_id_2)
        t.channel = "tg"
        db.commit()
    await _fake_fire(task_id_2, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # ONE TG chat session for both fires — the
        # whole point of the channel="tg" reuse branch.
        assert len(sessions) == 1
        tg_chat = sessions[0]
        assert tg_chat.channel == "tg"
        assert tg_chat.tgid == "9101"
        # Both cron prompts landed as user-messages
        # in this single session — that's the
        # conversation the agent and the operator's
        # TG bot share.
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=tg_chat.session_id)
            .all()
        )
        texts = [m.text for m in msgs]
        assert any("first-fire" in t for t in texts)
        assert any("second-fire" in t for t in texts)


async def test_tg_callback_wired_into_agent_loop(
    state_dir: Path,
) -> None:
    """When a bot is registered via
    :func:`set_telegram_bot`, the runner wires a
    ``_tg_send_callback`` into ``ToolContext`` so the
    agent's ``send_message`` tool can push to TG. The
    runner itself does NOT call ``bot.send_message``
    — the agent decides whether to call the tool
    (e.g. a "report if changed, otherwise stay silent"
    task might not push anything). The agent-loop's
    own ``send_message`` test (``test_handle_message_tg_calls_callback``)
    pins the end-to-end push; here we just verify the
    callback got wired in without crashing."""
    from magi.channels import telegram as _tg

    class _StubBot:
        async def send_message(self, *_args, **_kwargs):
            pass

    _tg.bot.set_telegram_bot(_StubBot())
    try:
        task_id = _seed_task(
            state_dir, "tg-wired", delivery_to="9101",
        )
        with open_session() as db:
            t = db.get(Task, task_id)
            t.channel = "tg"
            db.commit()

        # We patch handle_message to capture the kwargs
        # it received — we want to see that the
        # ``tg_send_callback`` is non-None for a tg
        # task. The agent loop is responsible for using
        # it via the send_message tool.
        import magi.agent.proactive.runner as runner_mod

        real = runner_mod.handle_message
        captured: dict = {}

        async def _capture(*_args, **kwargs):
            captured.update(kwargs)
            return "fake reply"

        runner_mod.handle_message = _capture  # type: ignore[assignment]
        try:
            await execute_task(str(state_dir), task_id, manual=True)
        finally:
            runner_mod.handle_message = real  # restore

        # The callback was wired (non-None when bot is
        # registered + channel is tg).
        callback = captured.get("tg_send_callback")
        assert callable(callback)
    finally:
        _tg.bot.clear_telegram_bot()
