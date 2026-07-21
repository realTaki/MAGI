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


# -- delivery_to = "new": fresh session per fire ----------------------------


async def test_delivery_to_new_creates_fresh_chat_session(state_dir: Path) -> None:
    """WebUI form's "new" default — every fire opens a
    fresh chat session so operator can scan cron replies
    in chat history."""
    task_id = _seed_task(state_dir, "fresh-every-fire", delivery_to="new")
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        # The seeded state has no pre-existing ChatSession
        # for this test → one fresh row produced by the
        # runner.
        assert len(sessions) == 1
        assert sessions[0].title.startswith("[定时]")
        # The cron reply lands as a user message (the agent
        # loop renders the assistant side on top; this test
        # only pins the inbound ChatMessage).
        msgs = db.query(ChatMessage).filter_by(session_id=sessions[0].session_id).all()
        assert any(m.role == "user" and "fresh-every-fire" in m.text for m in msgs)


# -- delivery_to = None: same path as "new" (legacy / unset) ----------------


async def test_delivery_to_null_also_creates_fresh_session(state_dir: Path) -> None:
    """Legacy rows (pre-DeliveryTarget) ship ``None`` in
    the column. The runner treats them identically to
    ``"new"`` — every fire spawns a fresh session."""
    task_id = _seed_task(state_dir, "legacy-row", delivery_to=None)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        sessions = db.query(ChatSession).all()
        assert len(sessions) == 1
        assert sessions[0].title.startswith("[定时]")


# -- delivery_to = <existing ULID>: reuse session -----------------------------


async def test_delivery_to_existing_session_reuses_it(state_dir: Path) -> None:
    """The LLM-in-chat path: ``delivery_to`` is set to
    the current session_id. The runner should NOT
    create a new ChatSession — it appends to the existing
    one and the cron reply joins the operator's ongoing
    conversation."""
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
        # Force-flush: SQLAlchemy 2.x needs the parent row
        # actually inserted before the child FK satisfies
        # (the dependency sort misses ChatSession→ChatMessage
        # within a single transaction in some cases). Without
        # flush, the FK violates even though the ChatSession
        # is queued.
        db.flush()
        # Also seed an existing message so we can confirm
        # the runner doesn't blow away earlier turns.
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
        # Still exactly one session — the same one. No new
        # row was created.
        assert len(sessions) == 1
        assert sessions[0].session_id == existing.session_id
        # Original title preserved (the runner doesn't
        # overwrite when reusing).
        assert sessions[0].title == "operator's ongoing chat"
        # Both the prior turn AND the new cron prompt
        # co-exist on this session.
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=existing.session_id)
            .all()
        )
        texts = [m.text for m in msgs]
        assert "earlier question" in texts
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
        delivery_to="01HXXXXXXXXXXXXXXXXXXXXXX",  # well-formed ULID
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
        # Operator sees it as a fresh "[定时] ghost-session"
        # — distinct from the missing target they thought
        # they were firing into.
        assert sessions[0].title.startswith("[定时]")

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
    runner can't trust a row's delivery_to alone — the
    row's ``employee_id`` has to match the session's
    ``employee_id``. We verify the runner re-falls-back
    to fresh session + the cross-employee session is
    untouched."""
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

        # Total ChatSession count: 1 (just the target, owned by
        # emp_b — emp_a's fresh session is the cross-employee
        # runner fallback that got created for emp_a's task).
        all_sessions = db.query(ChatSession).all()
        # 1 from the seeded-target + 1 from the fresh fallback.
        assert len(all_sessions) == 2
        # The fresh fallback session is owned by emp_a (the
        # task's rightful operator), not emp_b.
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
    runner.handle_message)."""
    import magi.agent.proactive.runner as runner_mod

    real = runner_mod.handle_message

    async def _noop(**_):
        return "fake reply"

    runner_mod.handle_message = _noop  # type: ignore[assignment]
    try:
        await execute_task(str(state_dir), task_id, manual=True)
    finally:
        runner_mod.handle_message = real  # restore
