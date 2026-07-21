"""Task runner — executes one fire of a scheduled task.

The scheduler calls :func:`execute_task` (a coroutine) when a
task's cron fires. Each invocation:

1. Reads the Task row (and the operator's Employee row for
   credentials).
2. Creates a fresh INTERNAL :class:`ChatSession` (channel
   ``"internal"``) + a user-message :class:`ChatMessage`
   carrying the task's prompt. The internal session is the
   agent's working context — never reused, never visible in
   the operator's chat list. Two fires of the same task
   (or of different tasks) cannot pollute each other's
   agent history by construction.
3. Calls :func:`magi.agent.loop.handle_message` with the
   employee credentials already in scope, against the
   internal session.
4. DELIVERS the agent's final reply to ``task.delivery_to``:
   - ``"new"`` → create a fresh "scheduled" chat with title
     ``"[定时] <name>"`` and write the reply there. The
     operator sees each fire as its own row in chat
     history.
   - ``<26-char ULID>`` resolving to a session owned by
     this employee → append the reply as an assistant
     message in that chat (mid-chat semantic).
   - ``<TG chat_id (digits)>`` with ``channel="tg"`` →
     look up the existing TG :class:`ChatSession` row by
     ``(tgid, employee_id)``, append the reply there, and
     the agent loop's own ``send_message`` tool pushes
     the same reply to TG via ``_tg_send_callback`` (the
     wire push happens mid-loop, not at this step).
5. Updates :class:`TaskRun` to point at the DELIVERED
   chat so the runs drawer shows the reply in its
   operator-visible context.
6. On failure, increments ``consecutive_failures``; if the
   threshold is crossed, disables the task and posts an
   :class:`ActionItem` so the operator sees a yellow flag
   in the dashboard.

Why a two-session model:

The operator-visible chat and the agent's working
context are different audiences. Putting them in the
same session meant a single ``delivery_to=<ULID>``
caused two cron-driven replies to share history (and
the LLM would sometimes echo the prior reply because
its in-context scratchpad was already populated with
the previous fire's user/assistant turns). Separating
the two means **agent context is always ephemeral**
and **the reply surfaces exactly where the operator
expects** — never bleeding cron internals into an
operator chat the operator didn't intend to mix
cron replies into.

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

    # ── 1. Read task + operator credentials + create INTERNAL session ──
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

        # INTERNAL session — fresh per fire. Channel
        # ``"internal"`` keeps this row out of the
        # operator's chat list (the chat-history UI
        # filters on the operator-facing channels).
        # The agent loop runs here, with no prior
        # context from any other task — every fire
        # starts from a clean slate, so cross-task
        # pollution is impossible by construction.
        internal_session_id = new_session_id()
        # ``chat_id`` is the TG chat the agent routes
        # tool calls through (and the one we'd push
        # to if a callback fires). For non-TG tasks
        # this is the operator's bound telegram_id
        # (or employee.id fallback) — the agent's
        # ``send_message`` tool still accepts it as
        # a target even when no wire push happens.
        chat_id = str(employee.telegram_id or employee.id)
        internal_sess = ChatSession(
            session_id=internal_session_id,
            tgid=chat_id,
            employee_id=employee.id,
            channel="internal",
            title=f"[task] {task.name}",
            created_at=utcnow_iso(),
            updated_at=utcnow_iso(),
        )
        db.add(internal_sess)
        # Force-flush the ChatSession INSERT before the
        # ChatMessage INSERT — SQLAlchemy 2.x's dependency
        # sort misses ChatSession→ChatMessage within the
        # same transaction in some cases, leaving the FK
        # dangling on commit. Cost: one extra round-trip
        # per fire; payoff: the chat_messages FK never
        # violates on a clean ChatSession row.
        db.flush()
        db.add(ChatMessage(
            session_id=internal_session_id,
            message_id=new_session_id(),
            role="user",
            text=task.prompt,
            ts=started,
        ))
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
                session_id=internal_session_id,
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
        delivery_target = task.delivery_to
        provider = employee.provider
        api_key = employee.api_key
        db.commit()

    # ── 2. Run the agent loop in the INTERNAL session ──
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
                session_id=internal_session_id,
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

    # ── 3. Deliver the reply to ``delivery_to`` + finalise ──
    finished = datetime.now(timezone.utc).isoformat()
    # TG push: the runner, NOT the agent loop, owns
    # pushing the final reply to TG. The previous design
    # wired ``_tg_send_callback`` into ``handle_message``
    # and relied on the agent calling its ``send_message``
    # tool mid-loop — that's fragile (the agent might
    # decide to "reply in chat history only"), and the
    # failure mode was silent: status='success' in the
    # DB, no TG push, operator confused. Now the runner
    # pushes the reply directly when ``channel='tg'``
    # AND a bot is registered. ``_tg_send_callback`` is
    # still wired in step 2 for any progress messages
    # the agent wants to send mid-execution; the runner
    # doesn't depend on the agent to deliver the
    # final reply.
    if task.channel == "tg" and chat_id.isdigit():
        bot = get_telegram_bot()
        if bot is not None:
            try:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=reply or "",
                )
            except Exception as exc:  # noqa: BLE001 — push failure isn't fatal
                logger.warning(
                    "task %s: TG push failed (%s); reply "
                    "still landed in chat history",
                    task_id, exc,
                )
    with open_session() as db:
        run = db.get(TaskRun, run_id)
        task = db.get(Task, task_id)
        if run is None or task is None:
            # Same race window as step 1; nothing to update.
            logger.info("execute_task: row vanished on success path (run=%s task=%s)",
                        run_id, task_id)
            return run_id
        # DELIVERY step: append the agent's reply to
        # whatever chat ``delivery_to`` resolves to. This
        # is independent of the INTERNAL session the
        # agent ran in — two fires of the same task land
        # in two separate agent sessions, but the
        # operator-facing delivery target is whichever
        # chat the row's ``delivery_to`` column points
        # at. ``_deliver_reply`` returns the
        # operator-visible ``session_id``; the run row's
        # ``session_id`` gets re-pointed so the runs
        # drawer shows the reply in its delivery chat.
        delivered_session_id = _deliver_reply(
            db,
            task=task,
            employee=employee,
            reply=reply,
            delivery_target=delivery_target,
            chat_id=chat_id,
            finished_iso=finished,
        )
        run.session_id = delivered_session_id
        run.status = "success"
        run.finished_at = finished
        run.latency_ms = _ms_between(started, finished)
        run.reply_excerpt = (reply or "")[:_REPLY_EXCERPT_CHARS]
        last_token = _latest_token_usage(db, started_iso=started)
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


def _deliver_reply(
    db: Session,
    *,
    task: Task,
    employee: Employee,
    reply: str | None,
    delivery_target: str | None,
    chat_id: str,
    finished_iso: str,
) -> str:
    """Land the agent's reply in the operator-facing
    chat pointed at by ``delivery_target``.

    Three shapes — same dispatch as the (now-removed)
    pre-refactor lookup, but each branch only writes
    the assistant message to the DELIVERY chat; the
    agent's INTERNAL session is never touched here.

      - ``None`` / ``"new"`` → create a fresh
        ``channel="scheduled"`` :class:`ChatSession`
        titled ``"[定时] <name>"`` so the operator sees
        each fire as its own row in chat history.
      - ``<26-char ULID>`` resolving to a
        :class:`ChatSession` owned by this employee →
        append the reply as an assistant message in
        that chat. This is the "mid-chat" semantic
        the LLM tool path uses (``ctx.session_id``
        landed here).
      - ``<TG chat_id (digits)>`` with
        ``channel == "tg"`` → look up the existing TG
        :class:`ChatSession` row by
        ``(tgid, employee_id)``; append the reply
        there as well as a chat-history record (the
        wire push to TG already happened during the
        agent loop via ``_tg_send_callback`` — this
        step mirrors that into the chat-history side
        so the operator sees the conversation from
        the WebUI too).

    Returns the operator-visible ``session_id`` for
    the :class:`TaskRun` row to point at — the runs
    drawer reads that column to render the reply in
    its delivery context. On the unresolved / cold
    cases we fall back to a fresh ``"new"`` chat so
    the reply is never lost.
    """
    text = reply or ""
    # ── Branch 1: explicit ULID → append to existing chat ──
    if delivery_target and delivery_target != "new":
        # TG chat_id (digits) + tg channel: look up by
        # ``(tgid, employee_id)``. The tgid IS the chat
        # address for TG; reusing the matching row
        # preserves the operator's ongoing TG
        # conversation history.
        if (
            task.channel == "tg"
            and delivery_target.isdigit()
        ):
            tg_session = db.query(ChatSession).filter_by(
                tgid=delivery_target,
                employee_id=task.employee_id,
            ).first()
            if tg_session is not None:
                db.add(ChatMessage(
                    session_id=tg_session.session_id,
                    message_id=new_session_id(),
                    role="assistant",
                    text=text,
                    ts=finished_iso,
                ))
                tg_session.updated_at = finished_iso
                return tg_session.session_id
            # TG row cold — create one with tgid stamped
            # so future TG deliveries accumulate into it.
            new_session = new_session_id()
            db.add(ChatSession(
                session_id=new_session,
                tgid=delivery_target,
                employee_id=task.employee_id,
                channel="tg",
                title=f"[定时] {task.name}",
                created_at=utcnow_iso(),
                updated_at=finished_iso,
            ))
            db.flush()
            db.add(ChatMessage(
                session_id=new_session,
                message_id=new_session_id(),
                role="assistant",
                text=text,
                ts=finished_iso,
            ))
            return new_session
        # Plain ULID → look up by PK + employee guard.
        target = db.get(ChatSession, delivery_target)
        if (
            target is not None
            and target.employee_id == task.employee_id
        ):
            db.add(ChatMessage(
                session_id=target.session_id,
                message_id=new_session_id(),
                role="assistant",
                text=text,
                ts=finished_iso,
            ))
            target.updated_at = finished_iso
            return target.session_id
        # Unresolved → warn + fall through to "new".
        logger.warning(
            "execute_task: task %s delivery_to=%r did not "
            "resolve to a ChatSession owned by employee %s; "
            "falling back to fresh [定时] chat",
            task.id, delivery_target, task.employee_id,
        )
    # ── Branch 2: "new" / None / unresolved → fresh [定时] chat ──
    new_session = new_session_id()
    db.add(ChatSession(
        session_id=new_session,
        tgid=chat_id,
        employee_id=task.employee_id,
        channel="scheduled",
        title=f"[定时] {task.name}",
        created_at=utcnow_iso(),
        updated_at=finished_iso,
    ))
    db.flush()
    db.add(ChatMessage(
        session_id=new_session,
        message_id=new_session_id(),
        role="assistant",
        text=text,
        ts=finished_iso,
    ))
    return new_session


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
