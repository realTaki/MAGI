"""Task runner — executes one fire of a scheduled task.

The scheduler calls :func:`execute_task` (a coroutine) when a
task's cron fires. Each invocation:

1. Reads the Task row (and the operator's Contact row for
   credentials). The task's home :class:`ChatSession`
   (``channel="task"``) was allocated at task creation
   time (see :mod:`magi.channels.api.tasks` and
   :mod:`magi.tools.schedule_task`); the runner
   just loads it via ``task.session_id`` and appends
   the prompt as a new user-message.

   If ``task.session_id`` is ``None`` (legacy row that
   pre-dates the column), the runner allocates one
   on the first fire and stamps it on the row —
   ensures every task ends up with one home session
   without requiring a separate migration.

2. Publishes a durable ``task.triggered`` input with the
   contact identity and task's home session. The agent worker sees the full
   history of prior fires' prompts + replies — same as
   a normal chat that happens to be triggered by a
   timer.

3. Lets the agent's :class:`magi.tools.send_message
   .SendMessageTool` route via the channel dispatcher
   (D.28). When ``task.delivery_to`` is a per-channel
   delivery address (a TG chat id today) and a bot is
   registered, the tool resolves the dispatcher's
   adapter and pushes the reply there. The runner does
   not wire a callback — the tool body owns its channel
   routing. v0 leaves the call site to the agent
   (system-prompt-mandated): a "report-if-changed,
   otherwise-stay-silent" task shouldn't push anything.

4. Pulls the latest TokenUsage row (the agent worker just
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
:class:`magi.channels.tasks.scheduler.TaskScheduler`)
decouples the two. See :class:`TaskScheduler` for the bridge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from magi.bus import AgentMessage, get_bus
from magi.bus.contracts.session import SessionMessage, new_session_id

# D.28: the runner no longer touches the TG client API
# directly. The channel dispatcher is the single dispatch
# point for "send a message to a user via a channel" — it
# resolves the session's channel to the right adapter, and
# the adapter (e.g. ``magi.channels.telegram.adapter``)
# does whatever IM-client wiring it needs. Importing the
# dispatcher is enough; we never reach for
# ``get_telegram_bot`` directly.
from magi.channels import Channel

logger = logging.getLogger("magi.channels.tasks.runner")

# Default failure-threshold before the runner auto-disables a
# task and posts an ActionItem. Override via the ``settings``
# KV table under key ``task.failure_threshold`` — read on
# every fire (no module import-time caching) so the operator
# can dial it down without restarting the scheduler.
_DEFAULT_FAILURE_THRESHOLD = 5

# Wall-clock budget on a single fire (seconds). Past this we
# give up the call and record a timeout; mirrors the
# 300-second running-task SLA documented in ``docs/proactive-architecture.md``.
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

    # ── 1. Read task + operator + load home session ──
    # LLM credentials are resolved inside
    # :func:`magi.providers.factory.get_provider` — the
    # runner never reads them and never passes them as
    # kwargs. The actor records ``LLMNotConfiguredError``
    # when the MAGI runtime isn't configured, which the
    # broad ``except Exception`` below logs as a
    # ``magi_missing_credentials`` task failure.
    bus = get_bus()
    execution = bus.task.prepare_execution(
        task_id=task_id, run_id=run_id, started_at=started, manual=manual,
    )
    if execution is None:
        logger.info("execute_task: task %s is unavailable for execution", task_id)
        return None

    schedule_desc = (
        execution.cron
        if execution.cron
        else (f"once at {execution.run_at}" if execution.run_at else "ad-hoc")
    )
    channel_directive = (
        f"If channel='tg', call the ``send_message`` tool with the reply text "
        f"to push the response. The delivery address resolves from "
        f"{execution.delivery_to or '(unset)'}. If channel='webui', the reply "
        f"lands inline in the operator's chat history automatically."
        if execution.target_channel == Channel.TG
        else "Channel='webui': the reply lands inline in the operator's chat history automatically."
    )
    contextual_prompt = (
        "[task context]\n"
        "You are EXECUTING a scheduled task that just fired. Do NOT call "
        "``schedule_task`` (or any tool that creates a new task). Carry out "
        "the prompt at the bottom as your goal for this fire.\n"
        f"name: {execution.task_name}\n"
        f"schedule: {schedule_desc}\n"
        f"channel: {execution.target_channel}\n"
        f"timezone: {execution.tz}\n"
        f"delivery_to: {execution.delivery_to or '(none — webui only)'}\n"
        f"delivery_directive: {channel_directive}\n\n"
        f"[task prompt]\n{execution.prompt}"
    )

    # Channel-owned delivery-address discovery remains outside BUS I/O.
    from magi.channels import dispatcher
    fallback_tg_im_id = dispatcher.lookup_im_id(execution.uid, Channel.TG) or ""
    bus.task.set_session_delivery_address(
        session_id=execution.session_id, delivery_address=fallback_tg_im_id,
    )
    bus.session.append_messages(
        execution.uid, execution.session_id,
        [SessionMessage(role="user", text=contextual_prompt, ts=started, message_id=new_session_id())],
        channel="task",
    )
    # ── 2. Publish the actor input ──
    # D.28: TG push is now handled by the channel
    # dispatcher. The agent loop never sees the TG
    # client API directly — it calls the dispatcher's
    # ``send_to_session`` (or the agent's own
    # ``send_message`` tool, which routes through the
    # dispatcher) and the dispatcher picks the right
    # adapter based on the session's channel column.
    # We pass ``channel="task"`` so the agent loop's
    # own tool-gating still applies (the tool only
    # activates for ``channel=Channel.TG``), but the
    # dispatcher picks the actual IM adapter at push
    # time. No callback injection here.

    try:
        bus.agent_runs.publish_input(
            AgentMessage(
                event_id=f"task:{task_id}:{run_id}",
                source_id=run_id,
                # Cross-channel idempotency triple (0009_idempotency_keys):
                # task_run_id is the upstream-stable id (one per scheduled
                # fire). A redelivered cron tick with a different local
                # event_id collapses to the same inbox row.
                source_type="task",
                external_event_id=run_id,
                kind="task.triggered",
                text=contextual_prompt,
                channel="task",
                uid=execution.uid,
                session_id=execution.session_id,
                caller_role=execution.caller_role,
                metadata={
                    "task_id": task_id,
                    "task_run_id": run_id,
                    "task_started_at": started,
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001 — durable publish failure
        logger.exception("task %s failed to publish actor input", task_id)
        bus.task.mark_execution_failure(
            task_id=task_id, run_id=run_id, started_at=started,
            error=f"publish:{type(exc).__name__}:{exc}",
        )
        return run_id

    # Completion/failure is projected by BusStore in the same transaction as
    # the actor transition.  This runner deliberately never waits for a reply.
    return run_id


# Public re-exports for the API / tests.
__all__ = ["execute_task"]
