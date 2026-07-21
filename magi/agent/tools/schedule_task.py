"""``schedule_task`` tool — LLM-callable task creation.

Public surface: the LLM can call this from any conversation
to set up a recurring check or alert.

Schema (v2 — preset + moment, no raw cron, no per-task
timezone, no per-task credentials):

  - ``name``        operator label, ≤120 chars
  - ``prompt``      natural-language instruction
  - ``frequency``   ``hourly`` / ``daily`` / ``weekly`` /
                     ``monthly`` / ``once``
  - ``hour``        0..23 (ignored for hourly, ignored for once)
  - ``minute``      0..59 (for hourly: fires every minute the
                     hour rolls; ignored for once)
  - ``day_of_week`` 0..6, Mon=0 (weekly only; ignored for once)
  - ``day_of_month`` 1..31 (monthly only; ignored for once)
  - ``run_at``      ISO 8601 timestamp; REQUIRED when
                     ``frequency="once"``. Naive timestamps
                     are interpreted as UTC. apscheduler
                     treats this as a single fire.
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

from magi.agent.proactive.cron_utils import preset_to_cron, validate_run_at, validate_run_at_future
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
                "enum": ["hourly", "daily", "weekly", "monthly", "once"],
                "description": (
                    "Preset cadence. The first four values "
                    "translate into a 5-field cron string "
                    "via the matching moment fields. ``\"once\"`` "
                    "is a one-shot task that fires at the "
                    "``run_at`` timestamp and never again; "
                    "moment fields are ignored."
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
            "run_at": {
                "type": "string",
                "description": (
                    "ISO 8601 timestamp (``YYYY-MM-DDTHH:MM:SS``, "
                    "optionally with offset like ``+08:00``). "
                    "REQUIRED when ``frequency='once'``; ignored "
                    "for recurring rows. Naive timestamps are "
                    "interpreted as UTC. apscheduler fires once "
                    "at this instant, then the task never "
                    "re-fires (no further cron). Example: "
                    "``\"2026-08-01T15:30:00+08:00\"``."
                ),
            },
            "channel": {
                "type": "string",
                "enum": ["webui", "tg"],
                "default": "webui",
                "description": (
                    "Where the fired reply surfaces. 'webui' "
                    "creates a chat session visible in the "
                    "operator's history list (each fire spawns "
                    "a fresh session unless the LLM called this "
                    "from inside an existing chat — then the "
                    "cron reply joins that chat). 'tg' "
                    "additionally lets the agent's send_message "
                    "tool push a reply to the operator's TG "
                    "chat (the runner looks up the existing TG "
                    "session by (chat_id, employee_id) and "
                    "reuses it; or uses the operator's bound "
                    "telegram_id when called from a non-TG "
                    "chat)."
                ),
            },
            # ``delivery_to`` was removed from the LLM-
            # facing schema: the tool no longer accepts a
            # caller-supplied destination. The server
            # derives it from channel + the caller's
            # ToolContext (session_id for webui, chat_id
            # for tg). The column stays on Task for
            # backward compat with rows created before
            # this unification.
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
        # ``channel`` is referenced up-front by the delivery_to
        # resolution block below (webui vs tg drives both the
        # default-rule branch and the format validator).
        channel = kwargs.get("channel") or "webui"
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
        if frequency not in ("hourly", "daily", "weekly", "monthly", "once"):
            return ToolResult(
                content=(
                    f"frequency must be one of "
                    f"hourly/daily/weekly/monthly/once, got {frequency!r}"
                ),
                is_error=True,
            )

        # ``delivery_to`` is server-derived per the unified
        # rule: only ``channel`` + ``ctx`` drive the value.
        #   channel='webui' + LLM-in-chat → ctx.session_id
        #     (append to the chat the LLM just wrote from)
        #   channel='webui' + cold call   → None (runner
        #     falls back; legacy / WebUI-default path stays
        #     as "fresh session per fire")
        #   channel='tg'    + LLM-in-TG  → ctx.chat_id (the
        #     TG chat the LLM is responding to)
        #   channel='tg'    + cold call  → None (runner
        #     falls back to operator.telegram_id at fire time)
        # The LLM does NOT choose; any caller-supplied
        # ``delivery_to`` is intentionally discarded (the
        # form is no longer a user-facing control, and a
        # stale LLM prompt that still passes one must not
        # override ctx).
        if channel == "webui":
            delivery_to = ctx.session_id or None
        elif channel == "tg":
            delivery_to = ctx.chat_id or None
        else:
            delivery_to = None

        # Branch on ``once`` vs the cron-driven presets.
        # ``cron`` and ``run_at`` are mutually exclusive on a
        # single Task row; the validator picks the active
        # shape at tool-call time. We translate at this
        # boundary so the WebUI API + LLM tool + raw SQL all
        # see the same row shape.
        run_at_iso: str | None = None
        if frequency == "once":
            try:
                run_at_iso = validate_run_at(
                    kwargs.get("run_at") or ""
                )
                # Past-time run_at silently no-ops in
                # apscheduler — reject here so the LLM
                # can retry with a future timestamp
                # rather than ship a dead task.
                validate_run_at_future(run_at_iso)
            except ValueError as exc:
                return ToolResult(
                    content=f"invalid run_at: {exc}",
                    is_error=True,
                )
            cron = ""  # sentinel: cron-driven cols blank
            # Moment fields (hour/minute/day_of_*) are
            # silently ignored for ``once`` — surfacing a
            # hard error would force the LLM to scrub the
            # same fields it just sent; soft ignore keeps
            # the contract tolerant.
        else:
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
                existing.run_at = run_at_iso
                existing.delivery_to = delivery_to
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
                    run_at=run_at_iso,
                    delivery_to=delivery_to,
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
    """Read the configured timezone; fall back to the
    server's local timezone (matches the canonical
    ``system_settings._system_default_timezone`` helper
    that ``GET /api/system-settings/timezone`` returns).

    Lazy import of ``system_settings`` to keep the tool
    module import graph small — the agent loop loads this
    file at chat-turn time, and the WebUI router pulls in
    SQLAlchemy / FastAPI / Pydantic that we don't need for
    pure cron handling. The helper function is reused
    verbatim; both the API endpoint and this tool now
    agree that "no configured timezone" means "use the
    server's local timezone", not hard-coded UTC.

    The ``os`` import is lazy for the same reason (don't
    force ``os.environ`` to be read at module load) — and
    for the state_dir path, prefer ``MAGI_STATE_DIR``
    with a fallback only for boot-time probes that
    pre-date the env var being set.
    """
    import os

    from magi.channels.webui.api.system_settings import (
        _system_default_timezone,
    )

    raw = state_get(
        os.environ.get("MAGI_STATE_DIR", "/workspace/memories"),
        "system.timezone",
    )
    if raw:
        try:
            ZoneInfo(raw)
            return raw
        except ZoneInfoNotFoundError:
            # Stored value isn't an IANA tz — fall through
            # to the server-local default. Same recovery
            # path the API uses.
            logger.warning(
                "schedule_task: stored system.timezone %r is "
                "not a valid IANA tz; falling back to %s",
                raw, _system_default_timezone(),
            )
    return _system_default_timezone()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ScheduleTaskTool"]
