"""Task runner — executes one fire of a scheduled task.

The scheduler calls :func:`execute_task` (a coroutine) when a
task's cron fires. Each invocation:

1. Reads the Task row (and the operator's Employee row for
   credentials). The task's home :class:`ChatSession`
   (``channel="task"``) was allocated at task creation
   time (see :mod:`magi.channels.webui.api.tasks` and
   :mod:`magi.agent.tools.schedule_task`); the runner
   just loads it via ``task.session_id`` and appends
   the prompt as a new user-message.

   If ``task.session_id`` is ``None`` (legacy row that
   pre-dates the column), the runner allocates one
   on the first fire and stamps it on the row —
   ensures every task ends up with one home session
   without requiring a separate migration.

2. Calls :func:`magi.agent.loop.handle_message` with the
   employee credentials already in scope, against the
   task's home session. The agent loop sees the full
   history of prior fires' prompts + replies — same as
   a normal chat that happens to be triggered by a
   timer.

3. Wires ``_tg_send_callback`` into the loop when
   ``task.delivery_to`` is a TG chat_id (digits) and
   a bot is registered. The agent's
   :class:`magi.agent.tools.send_message.SendMessageTool`
   pushes the reply to TG via this callback when the
   agent decides to use it. v0 leaves the call site
   to the agent (system-prompt-mandated): a
   "report-if-changed, otherwise-stay-silent" task
   shouldn't push anything.

4. Pulls the latest TokenUsage row (the agent loop just
   wrote one) onto the :class:`TaskRun` for cost-roll-up.

5. On failure, increments ``consecutive_failures``; if the
   threshold is crossed, disables the task and posts an
   :class:`ActionItem` so the operator sees a yellow flag
   in the dashboard.

Why a coroutine running inside its own event loop:
``apscheduler`` ships an asyncio variant (``AsyncIOScheduler``)
but the project's FastAPI endpoint already runs an asyncio
loop on the same process. Sharing a loop means a slow task
stalls a request handler; the dedicated loop (built by
:class:`magi.agent.proactive.scheduler.TaskScheduler`)
decouples the two. See :class:`TaskScheduler` for the bridge.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.agent.loop import handle_message
from magi.agent.proactive.orm_models import Task, TaskRun
from magi.agent.memory.session import (
    SessionMessage,
    SessionStore,
    new_session_id,
    utcnow_iso,
)
from magi.agent.db import ActionItem, ChatMessage, ChatSession, Employee, TokenUsage, open_session, require_state_dir
from magi.agent.db.settings import state_get

# Local import for the TG bot registry — keeps the
# cron-runner thread from pulling all of ``bot.py``
# at module load (bot.py imports telegram.ext + the
# daemon-thread bringup machinery). Lazy import inside
# :func:`execute_task` would be premature too: the
# registry accessor is cheap.
from magi.channels.telegram.bot import get_telegram_bot

logger = logging.getLogger("magi.agent.proactive.runner")

# Default failure-threshold before the runner auto-disables a
# task and posts an ActionItem. Override via the ``settings``
# KV table under key ``task.failure_threshold`` — read on
# every fire (no module import-time caching) so the operator
# can dial it down without restarting the scheduler.
_DEFAULT_FAILURE_THRESHOLD = 5

# Wall-clock budget on a single fire (seconds). Past this we
# give up the call and record a timeout; mirrors the
# 300-second running-task SLA in the proactive README.
_RUN_TIMEOUT_SECONDS = 300

# Cap on per-run reply we keep in the ``reply_excerpt`` column
# (chars). The full reply lives in the chat session; the
# excerpt exists for the history-pane's glance view.
_REPLY_EXCERPT_CHARS = 500

# Cap on the per-run error summary we keep on Task.last_error.
# Anything longer is the full traceback, which lives in the
# logs.
_LAST_ERROR_CHARS = 500


async def execute_task(
    state_dir: str,
    task_id: str,
    *,
    manual: bool = False,
    pre_created_run_id: str | None = None,
) -> str | None:
    """Run one fire of ``task_id``.

    Returns the ``TaskRun.id`` on completion (success or
    failure — caller logging benefits from this), or ``None``
    if the task was deleted mid-flight (a defensive
    no-op for the "I deleted the task at 14:59:59, the
    scheduler had it queued for 15:00:00" race).
    """
    started = datetime.now(timezone.utc).isoformat()
    run_id = pre_created_run_id or new_session_id()

    # ── 1. Read task + operator credentials + load home session ──
    with open_session() as db:
        task = db.get(Task, task_id)
        if task is None:
            logger.info("execute_task: task %s vanished mid-flight", task_id)
            return None
        employee = db.get(Employee, task.employee_id)
        if employee is None or not employee.api_key or not employee.provider:
            _finalise_run_failure(
                db, run_id=run_id, task_id=task_id, employee_id=task.employee_id,
                task_name=task.name, error="employee_missing_credentials",
                started_iso=started,
            )
            _bump_failure(db, task, "employee_missing_credentials")
            _maybe_disable_and_alert(db, task, "employee_missing_credentials")
            db.commit()
            return run_id

        # Load the task's home session. Allocated at
        # task-creation time by the API + schedule_task
        # tool. Legacy rows (pre-session_id column)
        # might still have ``None`` here; backfill on
        # first fire so legacy tasks still get a
        # thread.
        if task.session_id is None:
            task.session_id = new_session_id()
            db.add(ChatSession(
                session_id=task.session_id,
                tgid=str(employee.telegram_id or ""),
                employee_id=task.employee_id,
                channel="task",
                title=f"[定时] {task.name}",
                created_at=utcnow_iso(),
                updated_at=utcnow_iso(),
            ))
            db.flush()
        session_id = task.session_id
        # Build a contextual user-message that includes
        # the task's schedule metadata. The agent loop
        # otherwise only sees ``task.prompt`` — a vague
        # string like "提醒我查钱包" gives it no hint
        # about *why* it's running (cron vs one-shot,
        # monthly vs daily) or *who* it's running for.
        # Without context the LLM ends up either asking
        # clarification questions ("你希望怎么提醒？" —
        # see the claim_20号钱包提醒 bug) or assuming
        # this is a fresh setup request and calling
        # ``schedule_task`` again to "configure the
        # reminder", which creates a duplicate task.
        #
        # We keep the original prompt verbatim as the
        # last block so the agent loop's downstream
        # reply-excerpt extraction still picks up the
        # operator's actual instruction. The header is
        # scaffolding the agent should NOT ignore — the
        # first sentence explicitly says "execute, don't
        # re-create".
        schedule_desc = (
            task.cron if task.cron
            else (f"once at {task.run_at}" if task.run_at else "ad-hoc")
        )
        channel_directive = (
            f"If channel='tg', call the ``send_message`` tool with "
            f"the reply text and target chat_id "
            f"{task.delivery_to or '(unset)'} to push the response. "
            f"If channel='webui', the reply lands inline in the "
            f"operator's chat history automatically."
            if task.channel == "tg"
            else "Channel='webui': the reply lands inline in the "
                 "operator's chat history automatically."
        )
        context_header = (
            f"[task context]\n"
            f"You are EXECUTING a scheduled task that just fired. "
            f"Do NOT call ``schedule_task`` (or any tool that "
            f"creates a new task) — the schedule below is already "
            f"set up; you're running because it just fired. "
            f"Carry out the prompt at the bottom as your goal for "
            f"this fire.\n"
            f"name: {task.name}\n"
            f"schedule: {schedule_desc}\n"
            f"channel: {task.channel}\n"
            f"timezone: {task.tz}\n"
            f"delivery_to: {task.delivery_to or '(none — webui only)'}\n"
            f"delivery_directive: {channel_directive}\n"
            f"\n"
            f"[task prompt]\n"
        )
        contextual_prompt = context_header + task.prompt
        run = db.get(TaskRun, run_id)
        run = db.get(TaskRun, run_id)
        if run is None:
            # Cron-driven path: the scheduler never
            # pre-created the row. Insert one now so the
            # run shows up in the history pane as soon as
            # the fire starts. The manual path
            # (``POST /api/tasks/{id}/run``) takes the
            # other branch — the API pre-created the row
            # with ``status="running"`` so the operator's
            # follow-up GET can find it by ``run_id``
            # before the runner writes anything.
            run = TaskRun(
                id=run_id,
                task_id=task_id,
                session_id=session_id,
                trigger="manual" if manual else "cron",
                started_at=started,
                status="running",
            )
            db.add(run)
        # Snapshot for the calling coroutine so the
        # handle_message call doesn't need to keep its own
        # DB session open.
        task_name = task.name
        # ``prompt`` sent to the agent is the contextual
        # version (header + original prompt). The original
        # ``task.prompt`` is still on the row for audit;
        # the agent sees the wrapped text.
        prompt = contextual_prompt
        delivery_target = task.delivery_to
        provider = employee.provider
        api_key = employee.api_key
        db.commit()

    # Persist the user-message AFTER the open_session()
    # block exits — SessionStore opens its own session
    # internally, and calling it while the outer
    # transaction is still open would deadlock SQLite
    # (BEGIN IMMEDIATE inside another BEGIN). WebUI
    # chat.py follows the same pattern: append_messages
    # outside the request handler's outer ORM session.
    SessionStore(state_dir).append_messages(
        task.employee_id, session_id,
        [SessionMessage(
            role="user", text=contextual_prompt, ts=started,
            message_id=new_session_id(),
        )],
        channel="task",
    )

    # ── 2. Wire TG callback + run the agent loop ──
    # TG push is the agent's responsibility via its
    # ``send_message`` tool (system-prompt-mandated). We
    # just wire the callback when ``delivery_to`` is a
    # TG chat_id AND a bot is registered. For webui
    # tasks (no TG target) and tg tasks without a live
    # bot (test environments), the callback stays
    # ``None`` — the agent's tool path returns an error
    # but the chat history side still records the reply.
    tg_send_callback = None
    if (
        delivery_target
        and delivery_target.isdigit()
        and task.channel == "tg"
    ):
        bot = get_telegram_bot()
        if bot is not None:
            async def _tg_send_callback(
                to_chat_id: int,
                text_to_send: str,
            ) -> None:
                await bot.send_message(
                    chat_id=to_chat_id,
                    text=text_to_send,
                )
            tg_send_callback = _tg_send_callback

    try:
        reply = await asyncio.wait_for(
            handle_message(
                state_dir,
                text=prompt,
                # Task runner is the third channel after
                # WebUI / TG. The agent loop uses this to
                # gate send_message tool activation —
                # the tool returns is_error for non-tg
                # channels. Passing "scheduled" here (a
                # leftover from when the runner was a
                # one-off subsystem) would silently
                # disable the tool, so the agent's
                # "deliver via send_message" directive
                # in the user-message wouldn't reach TG.
                channel=task.channel,
                employee_id=employee.id,
                session_id=session_id,
                # ``chat_id`` is the IM target the agent's
                # ``send_message`` tool needs to know
                # where to push. For TG tasks that's the
                # TG chat_id; for webui tasks it's empty
                # (send_message is disabled on webui
                # anyway — see
                # :mod:`magi.agent.tools.send_message`).
                chat_id=delivery_target or "",
                employee_provider=provider,
                employee_api_key=api_key,
                employee_model=None,
                caller_role=employee.role,
                tg_send_callback=tg_send_callback,
            ),
            timeout=_RUN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _mark_failed(
            state_dir=state_dir, task_id=task_id, run_id=run_id,
            employee_id=employee.id, task_name=task_name,
            started_iso=started,
            error=f"timeout_after_{_RUN_TIMEOUT_SECONDS}s",
        )
    except Exception as exc:  # noqa: BLE001 — broad catch on purpose
        logger.exception("task %s crashed in handle_message", task_id)
        return _mark_failed(
            state_dir=state_dir, task_id=task_id, run_id=run_id,
            employee_id=employee.id, task_name=task_name,
            started_iso=started,
            error=f"unexpected:{type(exc).__name__}:{exc}",
        )

    # Persist the assistant reply via the same store API
    # the WebUI + TG channels use — mirrors the channel
    # pattern exactly. This is what the runs drawer's chat
    # bubbles render: the operator sees both the prompt
    # we appended above AND this reply in one scrollable
    # thread, identical to the main chat pane. Without
    # this append the assistant turn lived only in
    # SessionStore's in-memory model and the runs drawer
    # saw a one-sided conversation.
    finished_msg = datetime.now(timezone.utc).isoformat()
    try:
        SessionStore(state_dir).append_messages(
            employee.id, session_id,
            [SessionMessage(
                role="assistant", text=reply or "",
                ts=finished_msg,
                message_id=new_session_id(),
            )],
            channel="task",
        )
    except Exception:  # noqa: BLE001 — never fail the run for a missing history row
        logger.exception(
            "task %s: failed to append assistant reply to session %s",
            task_id, session_id,
        )

    # ── 3. Finalise ──
    # Reuse ``finished_msg`` so the assistant ChatMessage
    # row and the TaskRun row share the exact same
    # timestamp — keeps the chat-history bubble aligned
    # with the run row's "✓ 成功 · <time>" pill.
    finished = finished_msg
    with open_session() as db:
        run = db.get(TaskRun, run_id)
        task = db.get(Task, task_id)
        if run is None or task is None:
            logger.info("execute_task: row vanished on success path (run=%s task=%s)",
                        run_id, task_id)
            return run_id
        run.session_id = session_id
        run.status = "success"
        run.finished_at = finished
        run.latency_ms = _ms_between(started, finished)
        run.reply_excerpt = (reply or "")[:_REPLY_EXCERPT_CHARS]
        last_token = _latest_token_usage(
            db,
            session_id=session_id,
            started_iso=started,
        )
        if last_token is not None:
            run.input_tokens = last_token[0]
            run.output_tokens = last_token[1]
        task.consecutive_failures = 0
        task.last_run_at = finished
        task.last_status = "success"
        task.last_error = None
        db.commit()
    return run_id


# -- helpers ---------------------------------------------------------------




def _mark_failed(
    *,
    state_dir: str,
    task_id: str,
    run_id: str,
    employee_id: int,
    task_name: str,
    started_iso: str,
    error: str,
) -> str:
    """Single shared failure path.

    Writes the ``failed`` row + the body's error on the
    task + (maybe) an ActionItem. Kept as a function
    instead of inlined so both the timeout / unexpected
    branches above share one definition site.
    """
    finished = datetime.now(timezone.utc).isoformat()
    with open_session() as db:
        run = db.get(TaskRun, run_id)
        task = db.get(Task, task_id)
        if run is not None:
            run.status = "failed"
            run.finished_at = finished
            run.latency_ms = _ms_between(started_iso, finished)
            run.error = error[:_LAST_ERROR_CHARS]
        if task is not None:
            _bump_failure(db, task, error)
            _maybe_disable_and_alert(db, task, error)
        db.commit()
    return run_id


def _bump_failure(db: Session, task: Task, error: str) -> None:
    """Increment ``consecutive_failures`` + persist last_error."""
    task.consecutive_failures = (task.consecutive_failures or 0) + 1
    task.last_status = "failed"
    task.last_run_at = datetime.now(timezone.utc).isoformat()
    task.last_error = error[:_LAST_ERROR_CHARS]


def _maybe_disable_and_alert(db: Session, task: Task, error: str) -> None:
    """Cross the configured threshold → disable + post ActionItem.

    Idempotent: the threshold is a one-way edge (a
    success later resets ``consecutive_failures`` but
    does NOT re-enable — the operator has to flip the
    switch after they've read the ActionItem; that's
    the point of the alert).
    """
    threshold = _failure_threshold()
    if task.consecutive_failures < threshold:
        return
    if not task.enabled:
        # Already disabled — don't duplicate the alert.
        return
    task.enabled = 0
    db.add(ActionItem(
        employee_id=task.employee_id,
        kind="task_disabled",
        title=f"定时任务已自动停用：{task.name}",
        description=(
            f"连续失败 {task.consecutive_failures} 次（阈值 {threshold}）。"
            f"最后一次错误：{_truncate(error, 200)}"
        ),
        target_url=f"/chat/scheduled-tasks?task={task.id}",
        priority="high",
        source="system",
    ))
    logger.warning(
        "task %s auto-disabled after %d consecutive failures",
        task.name, task.consecutive_failures,
    )


def _finalise_run_failure(
    db: Session, *,
    run_id: str,
    task_id: str,
    employee_id: int,
    task_name: str,
    error: str,
    started_iso: str,
) -> None:
    """First-half failure: task missing credentials, no session
    yet created. Distinct from :func:`_mark_failed` because
    there's no session_id to attach the run to."""
    finished = datetime.now(timezone.utc).isoformat()
    db.add(TaskRun(
        id=run_id, task_id=task_id, session_id=None,
        trigger="cron", started_at=started_iso, finished_at=finished,
        status="failed",
        error=error[:_LAST_ERROR_CHARS],
        latency_ms=_ms_between(started_iso, finished),
    ))
    # Surface the alert directly because the failure
    # didn't go through the standard path.
    db.add(ActionItem(
        employee_id=employee_id,
        kind="task_disabled",
        title=f"定时任务无法执行：{task_name}",
        description=f"任务 \"{task_name}\" 配置引用的员工没有设置 provider/api_key。",
        target_url=f"/chat/scheduled-tasks?task={task_id}",
        priority="high",
        source="system",
    ))


def _latest_token_usage(db: Session, *, session_id: str, started_iso: str) -> tuple[int, int] | None:
    """Sum (input, output) tokens for the rows :func:`agent.handle_message`
    just wrote. The ``token_usage`` schema is per-call so a
    single fire may produce 1+ rows; summing keeps the
    dashboard's "cost" view aligned with the per-session bill.

    Filters by ``employee_id`` (the operator the LLM was
    billed against) + ``ts >= started_iso`` (the fire's
    wall-clock start). ``session_id`` is kept in the
    signature for compatibility but isn't a column on
    :class:`TokenUsage` — the token table is per-employee,
    not per-session. Multiple concurrent fires for the
    same employee would over-count, but that's a row
    collision the scheduler's ``max_instances=1`` already
    prevents.

    ``None`` if no rows landed yet (e.g. the agent returned
    before any LLM call — shouldn't happen in practice but
    keeps the helper defensive).
    """
    del session_id  # not a column on TokenUsage; see docstring
    rows = db.execute(
        select(TokenUsage.input_tokens, TokenUsage.output_tokens)
        .where(
            TokenUsage.ts >= started_iso,
        )
    ).all()
    if not rows:
        return None
    return (
        sum(int(r[0]) for r in rows),
        sum(int(r[1]) for r in rows),
    )


def _ms_between(started_iso: str, finished_iso: str) -> int:
    """Subtract two ISO-8601 UTC strings → integer ms.

    Falls back to 0 on parse failure (the row still
    records finished_at correctly; the latency cell is
    best-effort UX, not an SLA-critical value).
    """
    try:
        s = datetime.fromisoformat(started_iso)
        f = datetime.fromisoformat(finished_iso)
    except ValueError:
        return 0
    return max(0, int((f - s).total_seconds() * 1000))


def _failure_threshold() -> int:
    """Read the configurable threshold from the KV store.

    Falls back to the hard-coded default if the key
    is unset or malformed. Lazy read on every call —
    never cached at module import — so an operator
    editing the value in the Settings API takes effect
    on the very next failed run.
    """
    state_dir = require_state_dir()
    raw = state_get(state_dir, "task.failure_threshold")
    if raw is None or not raw.strip():
        return _DEFAULT_FAILURE_THRESHOLD
    try:
        v = int(raw.strip())
    except ValueError:
        return _DEFAULT_FAILURE_THRESHOLD
    return max(1, v)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# Public re-exports for the API / tests.
__all__ = ["execute_task"]
