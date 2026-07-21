"""Task runner — executes one fire of a scheduled task.

The scheduler calls :func:`execute_task` (a coroutine) when a
task's cron fires. Each invocation:

1. Reads the Task row (and the operator's Employee row for
   credentials).
2. Creates a brand-new :class:`ChatSession` plus a
   user-message :class:`ChatMessage` carrying the task's
   prompt.
3. Calls :func:`magi.agent.loop.handle_message` with the
   employee credentials already in scope.
4. Pulls the latest TokenUsage row (the agent loop just
   wrote one) onto the :class:`TaskRun` for cost-roll-up.
5. On failure, increments ``consecutive_failures``; if the
   threshold is crossed, disables the task and posts an
   :class:`ActionItem` so the operator sees a yellow flag in
   the dashboard.

Each fire gets its own session row (``channel="scheduled"``)
so the operator's history list shows every cron-driven run as
an independent line — matching the user's "each fire is its
own context, its own session" requirement.

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
from magi.agent.memory.session import new_session_id, utcnow_iso
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

    # ── 1. Read task + operator credentials + create session ──
    with open_session() as db:
        task = db.get(Task, task_id)
        if task is None:
            logger.info("execute_task: task %s vanished mid-flight", task_id)
            return None
        employee = db.get(Employee, task.employee_id)
        if employee is None or not employee.api_key or not employee.provider:
            # Credentials missing is a permanent failure;
            # treat it like any other: bump consecutive,
            # maybe disable, post one ActionItem.
            _finalise_run_failure(
                db, run_id=run_id, task_id=task_id, employee_id=task.employee_id,
                task_name=task.name, error="employee_missing_credentials",
                started_iso=started,
            )
            _bump_failure(db, task, "employee_missing_credentials")
            _maybe_disable_and_alert(db, task, "employee_missing_credentials")
            db.commit()
            return run_id

        # Each fire is its own session BY DEFAULT. The
        # operator can override this via ``task.delivery_to``:
        #
        #   - ``None`` (legacy pre-DeliveryTarget rows) or
        #     the literal ``"new"``: create a fresh chat
        #     session per fire, the surface the operator
        #     sees via the WebUI table as "[定时] <name>".
        #   - A 26-char ULID that resolves to an existing
        #     ``ChatSession``: reuse that session, append
        #     the cron prompt as a new ``ChatMessage`` row,
        #     and let ``handle_message`` continue from
        #     there. This is the mid-chat semantic — the
        #     operator's ongoing conversation accumulates
        #     cron replies without spawning a side thread.
        #   - ``task.channel == "tg"`` + ``delivery_to`` is a
        #     TG chat_id (digits): reuse the operator's
        #     existing TG ``ChatSession`` row by
        #     ``(tgid, employee_id)``, attach the cron
        #     prompt as a new ``ChatMessage``, and wire
        #     ``_tg_send_callback`` into the agent call so
        #     the agent's reply is pushed to the operator's
        #     TG chat. Sessions are keyed on tgid for TG
        #     (the chat itself is the address) so the
        #     reuse preserves the chat history.
        #   - ``task.channel == "email"``: not implemented in
        #     v0 (no runner branch) — falls back to the
        #     fresh-session shape and the reply lives in
        #     chat history.
        delivery_target = task.delivery_to
        session_id = new_session_id()
        # Default chat_id reflects the operator's TG
        # binding (mirrors today's behaviour for default
        # rows). The TG branch below overrides this when
        # an explicit chat_id is requested.
        chat_id = str(employee.telegram_id or employee.id)
        explicit_session = None
        if delivery_target and delivery_target != "new":
            # Resolve the target. Two shapes:
            #   - 26-char ULID (webui session_id) → look up
            #     by primary key.
            #   - Digits (TG chat_id) → look up by
            #     ``(tgid, employee_id)``; the chat_id
            #     gets stamped into the new chat session
            #     so the agent loop can route the reply
            #     back to the operator's TG chat.
            tg_match = (
                task.channel == "tg" and delivery_target.isdigit()
            )
            if tg_match:
                tg_session = db.query(ChatSession).filter_by(
                    tgid=delivery_target,
                    employee_id=employee.id,
                ).first()
                if tg_session is not None:
                    session_id = tg_session.session_id
                    chat_id = delivery_target
                    explicit_session = tg_session
                else:
                    logger.warning(
                        "execute_task: TG delivery_to=%s for task %s "
                        "did not match an existing ChatSession "
                        "(employee=%s); creating new session with "
                        "tgid stamped for the wire push",
                        delivery_target, task_id, employee.id,
                    )
                    # Fall through to fresh-session with
                    # the target TG chat_id stamped on the
                    # new row.
                    chat_id = delivery_target
            else:
                target_session = db.get(ChatSession, delivery_target)
                if (
                    target_session is not None
                    and target_session.employee_id == employee.id
                ):
                    # Reuse the existing chat — the cron
                    # reply joins the operator's ongoing
                    # conversation. We do NOT create a new
                    # ``ChatSession`` row, and we skip the
                    # title prefix (the existing title stays).
                    session_id = delivery_target
                    explicit_session = target_session
                else:
                    # Unresolved / cross-employee / non-ULID
                    # value: warn and fall through to new.
                    logger.warning(
                        "execute_task: task %s delivery_to=%r did "
                        "not resolve to a ChatSession owned by "
                        "employee %s; falling back to fresh session",
                        task_id, delivery_target, employee.id,
                    )
        if explicit_session is None:
            sess = ChatSession(
                session_id=session_id,
                tgid=chat_id,
                employee_id=employee.id,
                channel="scheduled",
                title=f"[定时] {task.name}",
                created_at=utcnow_iso(),
                updated_at=utcnow_iso(),
            )
            db.add(sess)
        # Force-flush the ChatSession INSERT before the
        # ChatMessage INSERT — SQLAlchemy 2.x's dependency
        # sort misses ChatSession→ChatMessage within the
        # same transaction in some cases, leaving the FK
        # dangling on commit. Cost: one extra round-trip
        # per fire; payoff: the chat_messages FK never
        # violates on a clean ChatSession row.
        db.flush()
        # Seed the user message so the agent loop sees the
        # prompt as the conversation's first turn when the
        # session is fresh, and as a new turn when the
        # session is reused (existing rows stay intact —
        # we only add, never delete/replace).
        db.add(ChatMessage(
            session_id=session_id,
            message_id=new_session_id(),
            role="user",
            text=task.prompt,
            ts=started,
        ))
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
        prompt = task.prompt
        provider = employee.provider
        api_key = employee.api_key
        db.commit()

    # ── 2. Run the agent loop ──
    # When channel is ``"tg"`` and the operator's bot is
    # running, wire a ``_tg_send_callback`` so the agent's
    # ``send_message`` tool actually pushes the reply to TG
    # (not just logs it in chat history). Without a live bot
    # (test environments, ``MAGI_STATE_DIR`` boots without TG
    # credentials), the loop runs but the TG reply stays in
    # chat history only.
    tg_send_callback = None
    if task.channel == "tg" and chat_id.isdigit():
        bot = get_telegram_bot()
        if bot is not None:
            target_chat_id = int(chat_id)

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
                channel="scheduled",
                employee_id=employee.id,
                session_id=session_id,
                chat_id=chat_id,
                employee_provider=provider,
                employee_api_key=api_key,
                employee_model=None,  # let the provider default
                # The fired task's owning operator's role.
                # Schedule-task owners are always
                # admin/assigned (that's what created the
                # task in the first place), but the loop
                # still wants the explicit role so its
                # tool-menu filter matches what the
                # operator would see if they sat at the
                # terminal instead.
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

    # ── 3. Success finalisation ──
    finished = datetime.now(timezone.utc).isoformat()
    with open_session() as db:
        run = db.get(TaskRun, run_id)
        task = db.get(Task, task_id)
        if run is None or task is None:
            # Same race window as step 1; nothing to update.
            logger.info("execute_task: row vanished on success path (run=%s task=%s)",
                        run_id, task_id)
            return run_id
        run.status = "success"
        run.finished_at = finished
        run.latency_ms = _ms_between(started, finished)
        run.reply_excerpt = (reply or "")[:_REPLY_EXCERPT_CHARS]
        last_token = _latest_token_usage(db, session_id=session_id, started_iso=started)
        if last_token is not None:
            run.input_tokens = last_token[0]
            run.output_tokens = last_token[1]
        # Reset the failure streak — one success clears it.
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

    ``None`` if no rows landed yet (e.g. the agent returned
    before any LLM call — shouldn't happen in practice but
    keeps the helper defensive).
    """
    rows = db.execute(
        select(TokenUsage.input_tokens, TokenUsage.output_tokens)
        .where(
            TokenUsage.session_id == session_id,
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
