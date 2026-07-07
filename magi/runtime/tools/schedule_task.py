"""``schedule_task`` tool — LLM-callable task creation.

Public surface: the LLM can call this from any conversation
to set up a recurring check or alert. Gate:

- **Admin only.** The tool refuses for non-admin
  employees (model role ``admin`` in the ``employees``
  table). This keeps an ``assigned``-role user from
  being able to schedule things on behalf of an
  organisation they administer-as-credential-bearer but
  not-as-policy-maker.
- **Idempotent on name.** A second call with the same
  ``name`` updates the existing row rather than creating
  a duplicate (the LLM retries often on transient
  errors).

Wire-up:
- Module path: ``magi.runtime.tools.schedule_task``.
- Registered into :func:`magi.runtime.tools.registry.get_tools`
  alongside the other built-ins; the registry's lazy
  import pattern means apscheduler isn't loaded for
  tests that don't touch scheduling.

The tool deliberately does NOT take ``employee_id`` —
the scheduler charges the current chat's operator's
credentials. ``channel`` defaults to ``"webui"``; the
operator can set ``tg`` if they want the reply pushed to
their TG chat (TG push is wired in the runner via the
existing ``send_message`` tool context, when the agent
loop decides to use it).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from magi.runtime.proactive.cron_utils import validate_cron
from magi.runtime.proactive.orm_models import Task
from magi.runtime.proactive.scheduler import get_scheduler
from magi.runtime.sessions import new_session_id
from magi.runtime.state.orm import Employee, open_session
from magi.runtime.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.runtime.tools.schedule_task")

_NAME_MAX = 120
_PROMPT_MAX = 8000


class ScheduleTaskTool(Tool):
    """Create or update a recurring scheduled task.

    Use when the operator asks you to set up a
    recurring check, daily summary, "every hour show me
    X", "Friday at 5pm tell me Y", etc.
    """

    name = "schedule_task"
    description = (
        "Create or update a recurring scheduled task. Requires "
        "admin scope. Each fire is an independent chat session; "
        "the conversation history shows every cron-driven reply "
        "as its own session under the operator's chat history."
        " Inputs: name (unique label ≤120 chars), prompt (the "
        "natural-language instruction to run each time), cron "
        "(5-field: '0 9 * * *' style — minute hour day month "
        "day_of_week), channel ('webui' or 'tg'), and an optional "
        "tz (IANA name, default 'UTC')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Short operator label, ≤120 chars. The same name "
                    "later updates the existing task instead of "
                    "creating a duplicate."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Natural-language instruction to run each fire. "
                    "The agent loop processes this as the user "
                    "message of a fresh session."
                ),
            },
            "cron": {
                "type": "string",
                "description": (
                    "5-field cron expression (minute hour day month "
                    "day_of_week). Examples: '*/5 * * * *' (every "
                    "5 min), '0 9 * * *' (daily 09:00), '0 9 * * "
                    "mon-fri' (weekdays 09:00)."
                ),
            },
            "channel": {
                "type": "string",
                "enum": ["webui", "tg"],
                "default": "webui",
                "description": (
                    "Where the fired reply surfaces. 'webui' creates "
                    "a chat session visible in the operator's history "
                    "list. 'tg' additionally lets the agent's "
                    "send_message tool push a reply to the operator's "
                    "TG chat."
                ),
            },
            "tz": {
                "type": "string",
                "default": "UTC",
                "description": "IANA timezone (e.g. 'UTC', 'Asia/Shanghai').",
            },
        },
        "required": ["name", "prompt", "cron"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        prompt = (kwargs.get("prompt") or "").strip()
        cron = (kwargs.get("cron") or "").strip()
        if not name or len(name) > _NAME_MAX:
            return ToolResult(
                content=f"name must be non-empty and ≤{_NAME_MAX} chars",
                is_error=True,
            )
        if not prompt or len(prompt) > _PROMPT_MAX:
            return ToolResult(
                content=f"prompt must be non-empty and ≤{_PROMPT_MAX} chars",
                is_error=True,
            )
        if not cron:
            return ToolResult(content="cron is required", is_error=True)
        # Cron validation runs synchronously — APScheduler
        # raises ValueError on malformed input.
        try:
            validate_cron(cron)
        except ValueError as exc:
            return ToolResult(content=f"invalid cron: {exc}", is_error=True)
        channel = kwargs.get("channel") or "webui"
        if channel not in ("webui", "tg"):
            return ToolResult(
                content=f"channel must be one of webui/tg, got {channel!r}",
                is_error=True,
            )
        tz = (kwargs.get("tz") or "UTC").strip() or "UTC"

        # ── Admin gate ────────────────────────────────────────────────
        # Read the operator's record directly. We
        # intentionally don't trust ``ctx.employee_id``
        # alone — the agent loop always populates it
        # but a future tool runner might call us from
        # the wrong context. Verifying against the DB
        # is cheap and closes that gap.
        with open_session() as db:
            emp = db.get(Employee, ctx.employee_id)
            if emp is None:
                return ToolResult(content="caller not found", is_error=True)
            if emp.role != "admin":
                return ToolResult(
                    content=(
                        "schedule_task requires admin scope; "
                        "the current operator is not an admin."
                    ),
                    is_error=True,
                )
            operator_id = emp.id

        # ── Idempotent upsert by name ──────────────────────────────────
        task_id = new_session_id()  # ULID — the helper's misnamed but correct
        is_update = False
        with open_session() as db:
            existing = db.execute(
                select(Task).where(Task.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                existing.prompt = prompt
                existing.cron = cron
                existing.tz = tz
                existing.channel = channel
                existing.enabled = 1
                existing.consecutive_failures = 0
                existing.employee_id = operator_id
                task_id = existing.id
                is_update = True
            else:
                db.add(Task(
                    id=task_id,
                    name=name,
                    prompt=prompt,
                    cron=cron,
                    tz=tz,
                    channel=channel,
                    employee_id=operator_id,
                    enabled=1,
                    consecutive_failures=0,
                    created_at=_now_iso(),
                    updated_at=_now_iso(),
                ))
            db.commit()

        # ── Live-register with the apscheduler singleton ───────────────
        # If the scheduler isn't running (e.g. tests,
        # or v0 single-node shipped without it), swallow
        # and return success — the DB row is the
        # source of truth and the next process restart
        # will pick the row up via ``_rehydrate_from_db``.
        try:
            scheduler = get_scheduler()
        except RuntimeError:
            logger.info(
                "schedule_task: scheduler not running; task %s stored in DB only",
                task_id,
            )
            return ToolResult(
                content=(
                    f"{'updated' if is_update else 'created'} task "
                    f"{name!r} (id={task_id}). Note: scheduler is not "
                    f"running; the task will activate on the next node start."
                )
            )
        # Re-read so we have a fully-populated Task to pass
        # to ``register`` (avoids the wrong-field-detached-instance
        # trap).
        with open_session() as db:
            task = db.get(Task, task_id)
            if task is not None:
                scheduler.register(task)
        return ToolResult(
            content=(
                f"{'updated' if is_update else 'created'} task "
                f"{name!r} (id={task_id}, cron={cron!r}, tz={tz!r}, "
                f"channel={channel!r})"
            )
        )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ScheduleTaskTool"]
