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
  - ``channel``     a registered delivery channel (default ``webui``)

Timezone + credentials come from the calling admin /
``assigned`` contact; the runner charges the operator's
own provider / API key. This mirrors the WebUI flow so
the operator's mental model stays consistent: "when this
fires, it runs as me".

Admin gate: callers whose effective role-tag set
(``Contact.role`` ∪ ``{admin}`` if
``ctx.bus.magis_admins_book.is_admin_for(contact_id=...)`` is
truthy) doesn't intersect ``{"admin", "assigned"}``
get ``is_error=True`` at the gate step. ``guest``
callers have no MAGI-node operator context and aren't expected
to chat.

Idempotent on ``name``: a second call with the same
name updates the existing row in place. The LLM retries
often on transient errors and we want a single
configurable task, not duplicates.
"""

from __future__ import annotations

import logging
from typing import Any

from old_bus.firmwares.books.local.tasksBook import preset_to_cron
from tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("tools.tasks.schedule")

# ``admin`` and ``assigned`` may see this tool in the
# catalog. ``guest`` is filtered out of the agent menu.


class ScheduleTaskTool(Tool):
    name = "schedule_task"

    # ``admin`` is catalog metadata, not a Contact.role value.
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Create or update a recurring scheduled task. Requires "
        "admin or assigned-contact scope (i.e. the calling "
        "operator is signed in to this MAGI). Each fire is an "
        "independent chat conversation; the conversation history "
        "shows every cron-driven reply as its own conversation under "
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
                    "message of a fresh conversation."
                ),
            },
            "frequency": {
                "type": "string",
                "enum": ["hourly", "daily", "weekly", "monthly", "once"],
                "description": (
                    "Preset cadence. The first four values "
                    "translate into a 5-field cron string "
                    'via the matching moment fields. ``"once"`` '
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
                "description": ("Only used when frequency='monthly'. 1..31."),
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
                    '``"2026-08-01T15:30:00+08:00"``.'
                ),
            },
            "channel": {
                "type": "string",
                "default": "webui",
                "description": (
                    "Where the fired reply surfaces. Use a registered "
                    "delivery channel (normally 'webui' or 'tg'). 'webui' "
                    "creates a chat conversation visible in the "
                    "operator's history list (each fire spawns "
                    "a fresh conversation unless the LLM called this "
                    "from inside an existing chat — then the "
                    "cron reply joins that chat). 'tg' "
                    "additionally lets the agent's send_message "
                    "tool push a reply to the operator's bound "
                    "TG chat (the runner looks up the existing "
                    "TG conversation by delivery address + contact_id "
                    "and reuses it; or uses the operator's bound "
                    "chat id when called from a non-TG chat)."
                ),
            },
            # ``delivery_to`` was removed from the LLM-
            # facing schema: the tool no longer accepts a
            # caller-supplied destination. The server
            # derives it from channel + the caller's
            # ToolContext (conversation_id for webui; the
            # operator's bound chat id for tg). The column
            # stays on Task for backward compat with rows
            # created before this unification.
        },
        "required": ["name", "prompt", "frequency"],
    }

    @Tool.require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:  # type: ignore[override]
        # Shape translation — kwargs → typed args.
        # ``name`` / ``prompt`` length & non-empty, plus
        # ``target_channel`` enum membership, are owned by
        # :meth:`TaskBook.add` (single source of truth, so
        # the dashboard API and any future agent-loop writer
        # get the same validation). Each violation surfaces
        # as ``ValueError``; we translate to LLM-facing
        # ``ToolResult.err`` after the Book call below.
        assert ctx.bus is not None, "require_bus should have caught this"
        name = (kwargs.get("name") or "").strip()
        prompt = (kwargs.get("prompt") or "").strip()
        frequency = (kwargs.get("frequency") or "").strip()
        # ``channel`` is referenced up-front by the delivery_to
        # resolution block below (webui vs tg drives both the
        # default-rule branch and the format validator).
        target_channel = kwargs.get("channel") or "webui"

        # ``delivery_to`` is server-derived per the unified
        # rule: only ``channel`` + ``ctx`` drive the value.
        #   channel='webui' + LLM-in-chat → ctx.conversation_id
        #     (append to the chat the LLM just wrote from)
        #   channel='webui' + cold call   → None (runner
        #     falls back; legacy / WebUI-default path stays
        #     as "fresh conversation per fire")
        #   channel='tg'    + LLM-in-TG  → ctx.delivery_address (the
        #     TG chat the LLM is responding to)
        #   channel='tg'    + cold call  → None (runner
        #     falls back to operator.tgid at fire time)
        # The LLM does NOT choose; any caller-supplied
        # ``delivery_to`` is intentionally discarded (the
        # form is no longer a user-facing control, and a
        # ``delivery_to`` resolution: the IM endpoint
        # for the new task. Webui tasks don't push to
        # anywhere external (the conversation is the visible
        # record; ``None`` is correct). TG tasks push
        # to wherever the calling conversation's IM target
        # lives — read it from the conversation row's
        # ``delivery_address`` column rather than carrying
        # a per-channel id through ctx (the conversation is
        # the source of truth for IM addressing, and
        # the dispatcher is the only thing that interprets
        # the value).
        if target_channel == "webui":
            delivery_to = None
        elif target_channel == "tg":
            delivery_to = ctx.bus.conversations_book.resolve_delivery_address(
                conversation_id=ctx.conversation_id
            )
        else:
            delivery_to = None

        # Branch on ``once`` vs the cron-driven presets.
        # ``cron`` and ``run_at`` are mutually exclusive on a
        # single Task row; we translate at this boundary so
        # the WebUI API + LLM tool + raw SQL all see the
        # same row shape. The Book owns the actual
        # validation (ISO parse + future-check + cron
        # expression check) — we just hand it the right
        # field and let any ValueError bubble up to the
        # outer ``ToolResult.err`` block below.
        cron: str | None
        run_at_iso: str | None
        if frequency == "once":
            cron = None
            run_at_iso = kwargs.get("run_at") or None
            # Moment fields (hour/minute/day_of_*) are
            # silently ignored for ``once`` — surfacing a
            # hard error would force the LLM to scrub the
            # same fields it just sent; soft ignore keeps
            # the contract tolerant.
        else:
            run_at_iso = None
            cron = preset_to_cron(
                frequency,  # type: ignore[arg-type]  # runtime-validated via kwargs.get
                hour=int(kwargs.get("hour") or 0),
                minute=int(kwargs.get("minute") or 0),
                day_of_week=kwargs.get("day_of_week"),
                day_of_month=kwargs.get("day_of_month"),
            )

        registered = ctx.bus.settings_book.channel_options()
        if target_channel not in registered or target_channel in {"a2a", "task"}:
            return ToolResult(
                content=f"channel is not a registered task delivery target: {target_channel!r}",
                is_error=True,
            )

        # Stamp the Contact-owned Telegram address on the new conversation as a
        # breadcrumb. Resolve it outside the task write transaction because
        # ContactBook uses its own short SQLite transaction.
        # Empty string when the operator has no TG binding.
        operator_id = int(ctx.contact_id)
        contact = ctx.bus.contacts_book.get(operator_id)
        task_conversation_delivery_address = (
            str(contact.tgid)
            if contact is not None and contact.tgid is not None
            else ""
        )

        # ── Idempotent upsert by name ──────────────────────────────────
        # Resolve system tz via the bus so the SQLAlchemy session
        # boundary stays in one place.
        resolved_tz = ctx.bus.settings_book.system_timezone()
        # Allocate the task's home conversation up-front so cron fires
        # accumulate into one conversation per task. The
        # ``upsert_by_name`` body preserves the existing
        # ``conversation_id`` for update-paths (continuity across
        # prompt edits).
        new_conversation_id_str = ctx.bus.conversations_book.create_task_conversation(
            contact_id=operator_id,
            title=f"[定时] {name}",
            delivery_address=task_conversation_delivery_address,
        )
        try:
            task_id, is_update = ctx.bus.tasks_book.upsert_by_name(
                name=name,
                prompt=prompt,
                cron=cron,
                run_at=run_at_iso,
                delivery_to=delivery_to,
                target_channel=target_channel,
                contact_id=operator_id,
                conversation_id=new_conversation_id_str,
                tz=resolved_tz,
            )
        except ValueError as e:
            # Book owns the write invariants (length caps,
            # channel enum, source enum). Translate the
            # ValueError to a clean LLM-facing error
            # rather than letting it bubble to the worker's
            # "tool.crashed" envelope.
            return ToolResult.err(str(e))

        return ToolResult(
            content=(
                f"{'updated' if is_update else 'created'} task "
                f"{name!r} (id={task_id}, frequency={frequency!r}, "
                f"cron={cron!r}, channel={target_channel!r})"
            )
        )


__all__ = ["ScheduleTaskTool"]
