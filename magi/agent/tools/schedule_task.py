"""``schedule_task`` tool — LLM-callable task creation.

Public surface: the LLM can call this from any conversation
to set up a recurring check or alert.

Schema (v2 — preset + moment, no raw cron, no per-task
timezone, no per-task credentials):

  - ``name``        operator label, ≤120 chars
  - ``prompt``      natural-language instruction
  - ``frequency``   ``hourly`` / ``daily`` / ``weekly`` / ``monthly``
  - ``hour``        0..23 (ignored for hourly)
  - ``minute``      0..59 (for hourly: fires every minute the
                     hour rolls)
  - ``day_of_week`` 0..6, Mon=0 (weekly only)
  - ``day_of_month`` 1..31 (monthly only)
  - ``channel``     ``webui`` / ``tg`` (default ``webui``)

Timezone + credentials come from the calling admin /
``assigned`` employee; the runner charges the operator's
own provider / API key. This mirrors the WebUI flow so
the operator's mental model stays consistent: "when this
fires, it runs as me".

Admin gate: non-admin / non-assigned employees get
``is_error=True``. Same logic as the API (``admin`` and
``assigned`` only — ``employee`` and ``guest`` are
barred since they don't sign in to a MAGI node).

Idempotent on ``name``: a second call with the same
name updates the existing row in place. The LLM retries
often on transient errors and we want a single
configurable task, not duplicates.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from magi.agent.proactive.cron_utils import preset_to_cron
from magi.agent.proactive.orm_models import Task
from magi.agent.proactive.scheduler import get_scheduler
from magi.agent.memory.session import new_session_id
from magi.agent.db import Employee, open_session
from magi.agent.db.settings import state_get
from magi.agent.tools.base import Tool, ToolContext, ToolResult
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("magi.agent.tools.schedule_task")

_NAME_MAX = 120
_PROMPT_MAX = 8000

# Same gate as the API: only ``admin`` and ``assigned``
# may create a task. ``employee`` and ``guest`` get
# ``is_error=True``.
_ROLE_MAY_CREATE = {"admin", "assigned"}


class ScheduleTaskTool(Tool):
    name = "schedule_task"

    # Visible only to ``admin`` and ``assigned`` operators.
    # Registry: ``get_tools(caller_role=...)`` strips this
    # tool out of the menu for everyone else, so the model
    # never learns it exists when it can't be invoked. The
    # in-run re-check below (``_ROLE_MAY_CREATE``) is a
    # defense-in-depth safeguard for the (currently
    # dormant) path that bypasses ``get_tools`` — better to
    # fail closed twice than to leak the tool's existence to
    # a caller who's not signed in to this MAGI node.
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Create or update a recurring scheduled task. Requires "
        "admin or assigned-employee scope (i.e. the calling "
        "operator is signed in to this MAGI). Each fire is an "
        "independent chat session; the conversation history "
        "shows every cron-driven reply as its own session under "
        "the operator's chat history. The task fires on "
        "the operator's system-wide timezone (configured in "
        "Settings → 系统时区). Inputs: name (unique label "
        "≤120 chars), prompt (the natural-language instruction "
        "to run each time), frequency ('hourly' / 'daily' / "
        "'weekly' / 'monthly'), hour (0..23, ignored when "
        "frequency='hourly'), minute (0..59), day_of_week "
        "(0..6 Mon=0, for weekly only), day_of_month (1..31, "
        "for monthly only), channel ('webui' / 'tg', default "
        "'webui')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Short operator label, ≤120 chars. The same "
                    "name later updates the existing task "
                    "instead of creating a duplicate."
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
            "frequency": {
                "type": "string",
                "enum": ["hourly", "daily", "weekly", "monthly"],
                "description": (
                    "Preset cadence. The server translates this "
                    "+ the matching moment fields into a canonical "
                    "5-field cron string; we don't accept raw cron."
                ),
            },
            "hour": {
                "type": "integer",
                "minimum": 0,
                "maximum": 23,
                "default": 0,
                "description": (
                    "Hour of day. Ignored when frequency='hourly'. "
                    "Combined with minute into the cron fire time."
                ),
            },
            "minute": {
                "type": "integer",
                "minimum": 0,
                "maximum": 59,
                "default": 0,
                "description": (
                    "Minute of hour. For hourly: 'fire at minute "
                    "X past every hour'. For daily/weekly/monthly: "
                    "the minute of the HH:MM fire time."
                ),
            },
            "day_of_week": {
                "type": "integer",
                "minimum": 0,
                "maximum": 6,
                "description": (
                    "Only used when frequency='weekly'. 0=Mon, "
                    "1=Tue, ..., 6=Sun (matches Python's "
                    "``datetime.weekday()`` convention)."
                ),
            },
            "day_of_month": {
                "type": "integer",
                "minimum": 1,
                "maximum": 31,
                "description": (
                    "Only used when frequency='monthly'. 1..31."
                ),
            },
            "channel": {
                "type": "string",
                "enum": ["webui", "tg"],
                "default": "webui",
                "description": (
                    "Where the fired reply surfaces. 'webui' "
                    "creates a chat session visible in the "
                    "operator's history list. 'tg' additionally "
                    "lets the agent's send_message tool push a "
                    "reply to the operator's TG chat."
                ),
            },
        },
        "required": ["name", "prompt", "frequency"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        prompt = (kwargs.get("prompt") or "").strip()
        frequency = (kwargs.get("frequency") or "").strip()
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
        if frequency not in ("hourly", "daily", "weekly", "monthly"):
            return ToolResult(
                content=f"frequency must be one of hourly/daily/weekly/monthly, got {frequency!r}",
                is_error=True,
            )

        # Translate the preset into canonical cron at the
        # tool's boundary so the tool API matches the WebUI
        # API exactly (and a malformed preset surfaces as
        # ``is_error=True`` rather than silently landing).
        try:
            cron = preset_to_cron(
                frequency,
                hour=int(kwargs.get("hour") or 0),
                minute=int(kwargs.get("minute") or 0),
                day_of_week=kwargs.get("day_of_week"),
                day_of_month=kwargs.get("day_of_month"),
            )
        except ValueError as exc:
            return ToolResult(content=f"invalid preset: {exc}", is_error=True)

        channel = kwargs.get("channel") or "webui"
        if channel not in ("webui", "tg"):
            return ToolResult(
                content=f"channel must be one of webui/tg, got {channel!r}",
                is_error=True,
            )

        # ── Admin / assigned gate ──────────────────────────────────────
        # Verify the calling operator. We pull role
        # from the DB (not ``ctx.employee_id``-trust) so
        # a mis-wired caller can't punch above its
        # authority.
        with open_session() as db:
            emp = db.get(Employee, ctx.employee_id)
            if emp is None:
                return ToolResult(content="caller not found", is_error=True)
            if emp.role not in _ROLE_MAY_CREATE:
                return ToolResult(
                    content=(
                        f"schedule_task requires admin or "
                        f"assigned-employee scope; "
                        f"role {emp.role!r} is not permitted."
                    ),
                    is_error=True,
                )
            operator_id = emp.id

        # ── Idempotent upsert by name ──────────────────────────────────
        is_update = False
        task_id = new_session_id()
        with open_session() as db:
            existing = db.execute(
                select(Task).where(Task.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                existing.prompt = prompt
                existing.cron = cron
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
                    tz=_resolve_system_tz(),
                    channel=channel,
                    employee_id=operator_id,
                    enabled=1,
                    consecutive_failures=0,
                    created_at=_now_iso(),
                    updated_at=_now_iso(),
                ))
            db.commit()

        # ── Live-register with the apscheduler singleton ───────────────
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
                    f"{name!r} (id={task_id}). Note: scheduler is "
                    f"not running; the task activates on next "
                    f"node start."
                )
            )
        with open_session() as db:
            task = db.get(Task, task_id)
            if task is not None:
                scheduler.register(task)
        return ToolResult(
            content=(
                f"{'updated' if is_update else 'created'} task "
                f"{name!r} (id={task_id}, frequency={frequency!r}, "
                f"cron={cron!r}, channel={channel!r})"
            )
        )


def _resolve_system_tz() -> str:
    """Read the configured timezone; default UTC.

    Tool path can run before the FastAPI endpoint so we
    don't import ``system_settings`` here — the KV table
    is the same one ``system.timezone`` writes.
    """
    raw = state_get(__import__("os").environ.get("MAGI_STATE_DIR", "/workspace/memories"), "system.timezone")
    if not raw:
        return "UTC"
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return "UTC"
    return raw


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ScheduleTaskTool"]
