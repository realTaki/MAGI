"""``add_action_item`` tool — record a new action item for
the calling operator.

Umbrella term: "todo", "task", "记一下", "待办" — all map
here. Creates one row per call (``contact_id=int(kwargs.get("contact_id") or 0)``). Re-calling
with the same title creates a *new* row — the operator
may want two parallel action items with similar titles;
we don't guess duplicates from a free-text title.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.action_items_book``
— the Book is pure CRUD and exposes ``add(...)`` plus
``to_dict`` on the returned DTO. ``source`` is decided by
the caller (default ``'user'``): chat-driven operator
tool calls leave it unset; scheduled tasks or agent
loops that reach this tool *without* an operator in the
loop pass ``source='proactive'`` so the provenance tag
reflects actual causation rather than the path the write
happened to take. The legacy service at
bus Book API is no longer
imported here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from old_bus.firmwares.books.local.actionItemBook import ActionItem, ActionPriority, ActionSource
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.tasks.add_action_item")


class AddActionItemTool(BaseTool):
    """Record a new action item for the calling operator."""

    name = "add_action_item"
    description = (
        "Add an action item for the operator (visible in the "
        "dashboard's Action Items pane). Use when the "
        "operator says '帮我记一下 X' / 'todo ...' / "
        "'记得下周要 Y'. Returns the created row's id. "
        "Inputs: title (required, ≤200 chars), "
        "description (optional, ≤1000 chars), priority "
        "('normal' default / 'high'), due_date "
        "(optional ISO date like '2026-07-30'), "
        "target_url (optional in-app link), source "
        "('user' default / 'proactive' — only set "
        "'proactive' when this tool was reached via a "
        "scheduled task / agent loop rather than a chat "
        "turn). Each call creates one row; close with "
        "complete_action_item."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": ("What to do, ≤200 chars. The operator-visible label."),
            },
            "description": {
                "type": "string",
                "description": (
                    "Optional detail, ≤1000 chars. Surfaces under the title in the dashboard."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high"],
                "default": "normal",
                "description": (
                    "'high' sorts above 'normal' in the "
                    "operator's dashboard list. Use "
                    "sparingly — the dashboard doesn't "
                    "have a colour differentiation yet, "
                    "it's just an ordering key."
                ),
            },
            "due_date": {
                "type": "string",
                "description": (
                    "Optional deadline in ISO date "
                    "format ('YYYY-MM-DD' or "
                    "'YYYY-MM-DDTHH:MM'). Null / "
                    "omitted means 'no deadline'. "
                    "The dashboard shows it alongside "
                    "the title; past-due items remain "
                    "visible — the operator dismisses "
                    "them manually."
                ),
            },
            "target_url": {
                "type": "string",
                "description": (
                    "Optional in-app path ('/dashboard?"
                    "tab=...') for the action item's "
                    "'go to' button. v0 only supports "
                    "relative paths; absolute URLs are "
                    "ignored at render time."
                ),
            },
            "source": {
                "type": "string",
                "enum": ["user", "proactive"],
                "default": "user",
                "description": (
                    "Provenance tag — who is the causal "
                    "head of this write? ``'user'`` (default) "
                    "for chat-driven operator tool calls, "
                    "``'proactive'`` when a scheduled "
                    "task / agent loop invoked this tool "
                    "without an operator in the loop. "
                    "Stamped onto the row so the dashboard "
                    "and audit trail reflect actual "
                    "causation, not the path the write "
                    "happened to take."
                ),
            },
        },
        "required": ["title"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        # Shape translation — turn kwargs into the typed
        # arguments :meth:`ActionItemBook.add` wants. The
        # Book owns the write invariants (non-empty title,
        # length caps, enum membership for ``priority`` and
        # ``source``) so we don't re-check those here. A
        # violation raises ValueError, which the worker
        # catches and surfaces as ``is_error=True`` to the
        # LLM.
        title = (kwargs.get("title") or "").strip()
        description = kwargs.get("description")
        priority = kwargs.get("priority") or ActionPriority.NORMAL
        target_url = kwargs.get("target_url")
        source = kwargs.get("source") or ActionSource.USER

        # ``due_date`` stays tool-side: the kwargs may carry
        # an ISO date string, but the Book expects a
        # ``datetime``. Parse leniently so callers can pass
        # either ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]``.
        due_date: datetime | None = None
        raw_due = kwargs.get("due_date")
        if raw_due is not None and str(raw_due).strip():
            raw = str(raw_due).strip()
            try:
                due_date = datetime.fromisoformat(raw)
            except ValueError:
                return ToolResult.err(
                    f"due_date must be a valid date (YYYY-MM-DD), got {raw!r}"
                )

        item_id = self.bus.action_items_book.add(ActionItem(
            contact_id=int(kwargs.get("contact_id") or 0),
            title=title,
            description=description,
            target_url=target_url,
            priority=priority,
            due_date=due_date,
            source=source,
        ))
        item = self.bus.action_items_book.get(item_id)

        logger.info(
            "add_action_item: item %s created for contact=%s title=%r source=%r",
            item.id,
            int(kwargs.get("contact_id") or 0),
            title,
            source,
        )
        return ToolResult.ok({"created": item.to_dict()})
