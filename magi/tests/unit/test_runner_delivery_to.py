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
from magi.agent.memory.session import new_session_id
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
                delivery_address=str(emp.telegram_id or ""),
                uid=emp.id,
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
            uid=emp.id,
            enabled=1,
            consecutive_failures=0,
            created_at="2026-07-20T12:00:00Z",
            updated_at="2026-07-20T12:00:00Z",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
    return t.id, session_id


# -- single session per task (channel="task") ----------------------------


async def test_fire_appends_prompt_and_reply_to_task_session(
    state_dir: Path,
) -> None:
    """The runner loads ``task.session_id`` (allocated
    at task creation) and persists BOTH the prompt and
    the agent's reply via ``SessionStore.append_messages``
    — same shape as the WebUI + TG channel paths so the
    runs drawer's chat bubbles render a full conversation
    instead of a one-sided prompt-only thread.

    Two tasks with the same name prefix → two separate
    sessions → cross-task pollution impossible by
    construction.
    """
    task_id, session_id = _seed_task(
        state_dir, "fresh-every-fire", delivery_to=None,
    )
    assert session_id is not None
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        # The task's session is exactly one row.
        task_sessions = (
            db.query(ChatSession)
            .filter_by(session_id=session_id)
            .all()
        )
        assert len(task_sessions) == 1
        task_sess = task_sessions[0]
        assert task_sess.channel == "task"
        assert task_sess.title.startswith("[定时]")
        # The session has BOTH turns of the conversation:
        # the user-message (prompt) and the assistant
        # reply ("fake reply" from _fake_fire). Cross-
        # channel guard D.22 is satisfied because
        # runner passes channel="task".
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=session_id)
            .all()
        )
        user_msgs = [m for m in msgs if m.role == "user"]
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        assert len(user_msgs) == 1
        assert "fresh-every-fire" in user_msgs[0].text
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].text == "fake reply"


async def test_two_tasks_have_independent_sessions(
    state_dir: Path,
) -> None:
    """Two tasks → two sessions. The runner never
    touches another task's session, so the two
    agents never see each other's history. This is
    the structural fix for the "two tasks both
    output 1 2 3" pollution bug: there's literally
    no shared session row for cross-fire leakage to
    happen through."""
    id_a, sess_a = _seed_task(state_dir, "task-aaaa", delivery_to=None)
    id_b, sess_b = _seed_task(state_dir, "task-bbbb", delivery_to=None)
    assert sess_a != sess_b  # different ULIDs

    await _fake_fire(id_a, state_dir)
    await _fake_fire(id_b, state_dir)

    with open_session() as db:
        sess_a_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=sess_a)
            .all()
        )
        sess_b_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=sess_b)
            .all()
        )
        # Each session only has its own task's prompt.
        # No leakage across sessions.
        assert any("task-aaaa" in m.text for m in sess_a_msgs)
        assert not any("task-bbbb" in m.text for m in sess_a_msgs)
        assert any("task-bbbb" in m.text for m in sess_b_msgs)
        assert not any("task-aaaa" in m.text for m in sess_b_msgs)


async def test_multiple_fires_accumulate_in_same_session(
    state_dir: Path,
) -> None:
    """Two fires of the same task → both prompts and
    both replies land in the same session (NOT two new
    sessions). The agent's "this is a continuing
    conversation" semantic — same as a normal chat
    that happens to be triggered by a timer.

    Each fire appends ONE user-message + ONE assistant
    reply, so the session grows by 2 rows per fire and
    the runs drawer renders 4 bubbles (prompt/reply,
    prompt/reply)."""
    task_id, session_id = _seed_task(
        state_dir, "recurring", delivery_to=None,
    )
    await _fake_fire(task_id, state_dir)
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        # Still exactly ONE session for this task.
        all_sessions = (
            db.query(ChatSession)
            .filter_by(session_id=session_id)
            .all()
        )
        assert len(all_sessions) == 1
        # Two fires × (user + assistant) = 4 messages.
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=session_id)
            .all()
        )
        user_msgs = [m for m in msgs if m.role == "user"]
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        assert len(user_msgs) == 2
        assert len(assistant_msgs) == 2
        assert all("recurring" in m.text for m in user_msgs)
        # Replies match the no-op fake ("fake reply" from
        # _fake_fire).
        assert all(m.text == "fake reply" for m in assistant_msgs)


# -- legacy rows: session_id None at fire time ---------------------------


async def test_legacy_task_without_session_id_backfills_on_first_fire(
    state_dir: Path,
) -> None:
    """Legacy rows that pre-date the ``session_id``
    column ship with ``task.session_id = None``. The
    runner allocates a fresh channel="task" session
    on first fire and stamps it on the row, so the
    task still gets a thread for the agent and the
    operator's chat history. Subsequent fires reuse
    it."""
    task_id, _ = _seed_task(
        state_dir, "legacy-row", delivery_to=None,
        with_session=False,
    )
    with open_session() as db:
        t = db.get(Task, task_id)
        assert t.session_id is None

    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        t = db.get(Task, task_id)
        assert t.session_id is not None  # backfilled
        # The session exists and has the prompt.
        sess = db.get(ChatSession, t.session_id)
        assert sess is not None
        assert sess.channel == "task"
        msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=t.session_id)
            .all()
        )
        assert any("legacy-row" in m.text for m in msgs)


# -- delivery_to=ULID: obsolete, runner ignores it ---------------------


async def test_legacy_delivery_to_ulid_is_ignored(
    state_dir: Path,
) -> None:
    """Legacy rows with ``delivery_to="<ULID>"`` (the
    pre-refactor "join my chat" semantic) are now
    inert — the runner ignores the value and uses
    ``task.session_id`` instead. The legacy ULID
    chat is untouched (no cross-employee injection).
    """
    with open_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=9101).one()
        legacy = ChatSession(
            session_id="01HABCDEFGHJKMNPQRSTVWXY",
            delivery_address=str(emp.telegram_id),
            uid=emp.id,
            channel="webui",
            title="operator's ongoing chat",
            created_at="2026-07-20T09:00:00Z",
            updated_at="2026-07-20T11:00:00Z",
        )
        db.add(legacy)
        db.flush()
        db.add(ChatMessage(
            session_id=legacy.session_id,
            message_id="m_prior",
            role="user",
            text="earlier question",
            ts="2026-07-20T10:00:00Z",
        ))
        db.commit()

    task_id, task_session_id = _seed_task(
        state_dir, "legacy-ulid",
        delivery_to=legacy.session_id,  # legacy shape
    )
    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        # The legacy chat is untouched.
        msgs_legacy = (
            db.query(ChatMessage)
            .filter_by(session_id=legacy.session_id)
            .all()
        )
        assert len(msgs_legacy) == 1
        assert msgs_legacy[0].text == "earlier question"
        # The task's prompt landed in the task's session,
        # NOT in the legacy chat.
        msgs_task = (
            db.query(ChatMessage)
            .filter_by(session_id=task_session_id)
            .all()
        )
        assert any("legacy-ulid" in m.text for m in msgs_task)


# -- cross-employee: delivery_to=None / task.session_id is task-owned ------


async def test_cross_employee_does_not_inject_into_other(
    state_dir: Path,
) -> None:
    """Task A's session is owned by employee A; the
    runner never writes to a session belonging to a
    different employee. With the new model this is
    automatic (task.session_id is owned by the task's
    employee), so we just verify the session
    ownership holds."""
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

    # Task owned by emp_a; the runner's session is
    # stamped to emp_a only.
    task_id, session_id = _seed_task(
        state_dir, "a-owns", delivery_to=None,
    )
    with open_session() as db:
        sess = db.get(ChatSession, session_id)
        assert sess is not None
        assert sess.uid != emp_b.id

    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        # Only emp_b's seeded rows + emp_a's task
        # session exist; no spill into emp_b's rows.
        other_sessions = (
            db.query(ChatSession)
            .filter_by(uid=emp_b.id)
            .all()
        )
        assert len(other_sessions) == 0


# -- TG delivery_to: wires callback --------------------------------------


async def test_tg_delivery_to_wires_callback(
    state_dir: Path,
) -> None:
    """When ``task.channel='tg'`` and
    ``task.delivery_to`` is a TG delivery_address (digits) and
    a bot is registered, the runner wires
    ``_tg_send_callback`` into the agent loop. The
    callback is the agent's responsibility to invoke
    via ``send_message`` — the runner doesn't push
    itself. This is the structural fix for the
    "task fires successfully but TG doesn't receive"
    bug: the callback is wired every fire so any
    ``send_message`` call from the agent reaches TG."""
    from magi.channels import telegram as _tg

    captured: dict = {}

    class _StubBot:
        async def send_message(self, *, delivery_address, text, **_kwargs):
            captured["delivery_address"] = delivery_address
            captured["text"] = text

    _tg.bot.set_telegram_bot(_StubBot())
    try:
        task_id, _ = _seed_task(
            state_dir, "tg-callback",
            channel="tg",
            delivery_to="9101",
        )
        # Patch handle_message to capture the kwargs.
        import magi.agent.proactive.runner as runner_mod
        real = runner_mod.handle_message

        async def _capture(*_args, **kwargs):
            captured["tg_send_callback"] = kwargs.get("tg_send_callback")
            captured["delivery_address"] = kwargs.get("delivery_address")
            return "fake reply"

        runner_mod.handle_message = _capture  # type: ignore[assignment]
        try:
            await execute_task(str(state_dir), task_id, manual=True)
        finally:
            runner_mod.handle_message = real

        # The callback was wired (not None). D.26 dropped
        # ``delivery_address`` from ``handle_message`` — the LLM tools
        # (send_message in particular) read the per-channel
        # delivery address directly from
        # ``chat_sessions.delivery_address`` instead. The callback closure
        # captures the target delivery_address at fire time and uses it
        # as the ``delivery_address=`` kwarg on the underlying bot call.
        cb = captured.get("tg_send_callback")
        assert callable(cb)
        assert captured.get("delivery_address") is None
    finally:
        _tg.bot.clear_telegram_bot()


async def test_tg_session_is_not_modified_by_task_fire(
    state_dir: Path,
) -> None:
    """The runner never touches the TG chat session
    (``channel='tg'``) — task fires accumulate into
    the task's own ``channel='task'`` session. The
    TG session is for the operator's TG chat with
    the bot; task fires are a separate thread."""
    with open_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=9101).one()
        tg_chat = ChatSession(
            session_id="01HTGCHATSESSIONXXXXXXXXX",
            delivery_address="9101",
            uid=emp.id,
            channel="tg",
            title="operator's TG chat",
            created_at="2026-07-20T09:00:00Z",
            updated_at="2026-07-20T11:00:00Z",
        )
        db.add(tg_chat)
        db.flush()
        db.add(ChatMessage(
            session_id=tg_chat.session_id,
            message_id="m_tg_prior",
            role="user",
            text="hi bot",
            ts="2026-07-20T10:00:00Z",
        ))
        db.commit()

    task_id, task_session_id = _seed_task(
        state_dir, "tg-keeps-clean",
        channel="tg",
        delivery_to="9101",
    )

    await _fake_fire(task_id, state_dir)

    with open_session() as db:
        # TG chat session: exactly one message (the
        # prior turn), the runner didn't append anything.
        tg_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=tg_chat.session_id)
            .all()
        )
        assert len(tg_msgs) == 1
        assert tg_msgs[0].text == "hi bot"
        # Task session: has the prompt.
        task_msgs = (
            db.query(ChatMessage)
            .filter_by(session_id=task_session_id)
            .all()
        )
        assert any("tg-keeps-clean" in m.text for m in task_msgs)
        # The TG chat session and the task session are
        # different rows; the TG session was untouched.
        assert tg_chat.session_id != task_session_id


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


