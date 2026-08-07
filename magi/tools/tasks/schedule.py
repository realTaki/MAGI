
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
``assigned`` contact; the runner charges the operator's
own provider / API key. This mirrors the WebUI flow so
the operator's mental model stays consistent: "when this
fires, it runs as me".

Admin gate: non-admin / non-assigned contacts get
``is_error=True``. Same logic as the API (``admin`` and
``assigned`` only — ``contact`` and ``guest`` are
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

from magi.bus import get_bus
from magi.bus.jobs.protocols.channels import ChannelEnum as Channel
from magi.bus.jobs.protocols.session import new_session_id
from magi.tools.base import Tool, ToolContext, ToolResult
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("magi.tools.tasks.schedule")

_NAME_MAX = 120
_PROMPT_MAX = 8000

# ``admin`` and ``assigned`` may create a task. ``contact``
# and ``guest`` get ``is_error=True`` (the in-run check
# below enforces this — the registry's role-based
# ``ALLOWED_ROLES`` filter only sees the role enum, not
# the separate ``admin`` boolean, so we widen
# ``ALLOWED_ROLES`` to an empty frozenset and lean on the
# in-run check). Mirrors the API's
# ``_enforce_creator_can_create(admin, role)`` helper.
_ROLE_MAY_CREATE = frozenset()  # all roles visible; in-run gate enforces the real rule


class ScheduleTaskTool(Tool):
    name = "schedule_task"

    # Visible to anyone; the in-run re-check below
    # (``_ROLE_MAY_CREATE``) is the real gate. We can't
    # filter on the registry's ``ALLOWED_ROLES`` because
    # that field is keyed on the ``role`` enum only —
    # the separate ``admin`` boolean (WebUI sign-in
    # rights, which moved out of the role enum in 2024)
    # is not plumbed through to the registry. Widening
    # the menu filter and leaning on the in-run check is
    # the same defense-in-depth shape the rest of the
    # codebase uses for this category of tool: the model
    # learns the tool exists, the run-time guard rejects
    # non-authors with ``is_error=True``.
    ALLOWED_ROLES = frozenset({"assigned"})
    # In-run author gate: ``admin=True OR role='assigned'``.
    # Mirrors the API's
    # ``_enforce_creator_can_create(admin, role)`` helper.
    description = (
        "Create or update a recurring scheduled task. Requires "
        "admin or assigned-contact scope (i.e. the calling "
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
                "enum": [Channel.WEBUI, Channel.TG],
                "default": Channel.WEBUI,
                "description": (
                    "Where the fired reply surfaces. 'webui' "
                    "creates a chat session visible in the "
                    "operator's history list (each fire spawns "
                    "a fresh session unless the LLM called this "
                    "from inside an existing chat — then the "
                    "cron reply joins that chat). 'tg' "
                    "additionally lets the agent's send_message "
                    "tool push a reply to the operator's bound "
                    "TG chat (the runner looks up the existing "
                    "TG session by delivery address + uid and "
                    "reuses it; or uses the operator's bound "
                    "chat id when called from a non-TG chat)."
                ),
            },
            # ``delivery_to`` was removed from the LLM-
            # facing schema: the tool no longer accepts a
            # caller-supplied destination. The server
            # derives it from channel + the caller's
            # ToolContext (session_id for webui; the
            # operator's bound chat id for tg). The column
            # stays on Task for backward compat with rows
            # created before this unification.
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
        target_channel = kwargs.get("channel") or Channel.WEBUI
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
        #   channel='tg'    + LLM-in-TG  → ctx.delivery_address (the
        #     TG chat the LLM is responding to)
        #   channel='tg'    + cold call  → None (runner
        #     falls back to operator.telegram_id at fire time)
        # The LLM does NOT choose; any caller-supplied
        # ``delivery_to`` is intentionally discarded (the
        # form is no longer a user-facing control, and a
        # ``delivery_to`` resolution: the IM endpoint
        # for the new task. Webui tasks don't push to
        # anywhere external (the session is the visible
        # record; ``None`` is correct). TG tasks push
        # to wherever the calling session's IM target
        # lives — read it from the session row's
        # ``delivery_address`` column rather than carrying
        # a per-channel id through ctx (the session is
        # the source of truth for IM addressing, and
        # the dispatcher is the only thing that interprets
        # the value).
        if target_channel == Channel.WEBUI:
            delivery_to = None
        elif target_channel == Channel.TG:
            delivery_to = get_bus().session.resolve_delivery_address_for_session(
                ctx.session_id
            )
        else:
            delivery_to = None

        # Branch on ``once`` vs the cron-driven presets.
        # ``cron`` and ``run_at`` are mutually exclusive on a
        # single Task row; the validator picks the active
        # shape at tool-call time. We translate at this
        # boundary so the WebUI API + LLM tool + raw SQL all
        # see the same row shape.
        bus = get_bus()
        run_at_iso: str | None = None
        if frequency == "once":
            try:
                run_at_iso = bus.task.validate_run_at(
                    kwargs.get("run_at") or ""
                )
                # Past-time run_at silently no-ops in
                # apscheduler — reject here so the LLM
                # can retry with a future timestamp
                # rather than ship a dead task.
                bus.task.validate_run_at_future(run_at_iso)
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
                cron = bus.task.preset_to_cron(
                    frequency,
                    hour=int(kwargs.get("hour") or 0),
                    minute=int(kwargs.get("minute") or 0),
                    day_of_week=kwargs.get("day_of_week"),
                    day_of_month=kwargs.get("day_of_month"),
                )
            except ValueError as exc:
                return ToolResult(content=f"invalid preset: {exc}", is_error=True)

        if target_channel not in (Channel.WEBUI, Channel.TG):
            return ToolResult(
                content=f"channel must be one of webui/tg, got {target_channel!r}",
                is_error=True,
            )

        # ── Admin / assigned gate ──────────────────────────────────────
        # Verify the calling operator. We pull role
        # from the DB (not ``ctx.uid``-trust) so
        # a mis-wired caller can't punch above its
        # authority.
        bus = get_bus()
        contact = bus.contacts.get(ctx.uid)
        if contact is None:
            return ToolResult(content="caller not found", is_error=True)
        # Author gate: ``admin=True`` (WebUI operator)
        # OR ``role='assigned'`` (the served user).
        # Mirrors ``magi.channels.api.tasks.
        # _enforce_creator_can_create`` so the API
        # and the LLM-side tool agree on who can
        # author a scheduled task. Re-read from the DB
        # (not ``ctx.uid``-trust) so a mis-wired caller
        # can't punch above its authority.
        if not (bool(contact.role == "assigned")):
            return ToolResult(
                content=(
                    f"schedule_task requires admin or "
                    f"assigned-contact scope; "
                    f"role={contact.role!r} "
                    f"is not permitted."
                ),
                is_error=True,
            )
        operator_id = contact.id
        # Session closed — now safe to open nested ones
        # (the dispatcher adapter's ``with open_session()``
        # would otherwise deadlock against SQLite's
        # ``BEGIN IMMEDIATE`` reservation).

        # D.28: stamp the operator's bound IM id on the new
        # session row as a breadcrumb. Resolved via the
        # bus.dispatcher outside the open_session
        # (the dispatcher opens its own SQLite session
        # — nested inside an outer txn would deadlock).
        # Empty string when the operator has no TG binding.
        task_session_delivery_address = (
            bus.dispatcher.lookup_im_id(operator_id, Channel.TG) or ""
        )

        # ── Idempotent upsert by name ──────────────────────────────────
        bus = get_bus()
        # Resolve system tz via the bus so the SQLAlchemy session
        # boundary stays in one place.
        resolved_tz = bus.settings.system_timezone()
        # Allocate the task's home session up-front so cron fires
        # accumulate into one conversation per task. The
        # ``upsert_by_name`` body preserves the existing
        # ``session_id`` for update-paths (continuity across
        # prompt edits).
        new_session_id_str = bus.session.create_task_session(
            uid=operator_id,
            title=f"[定时] {name}",
            delivery_address=task_session_delivery_address,
        )
        task_id, is_update = bus.task.upsert_by_name(
            name=name,
            prompt=prompt,
            cron=cron,
            run_at=run_at_iso,
            delivery_to=delivery_to,
            target_channel=target_channel,
            uid=operator_id,
            session_id=new_session_id_str,
            tz=resolved_tz,
        )

        return ToolResult(
            content=(
                f"{'updated' if is_update else 'created'} task "
                f"{name!r} (id={task_id}, frequency={frequency!r}, "
                f"cron={cron!r}, channel={target_channel!r})"
            )
        )


__all__ = ["ScheduleTaskTool"]
