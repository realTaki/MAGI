"""``add_action_item`` tool — record a new action item for
the calling operator.

Umbrella term: "todo", "task", "记一下", "待办" — all map
here. Creates one row per call (``kind='llm_action_item_<id>'``,
``source='llm'``, ``uid=ctx.uid``). Re-calling with the
same title creates a *new* row — the operator may want two
parallel action items with similar titles; we don't guess
duplicates from a free-text title.

Scope (per-contact, role-gated): only ``admin`` (per
:attr:`ctx.bus.magis_admins_book`) and ``assigned`` (per
``Contact.role``) operators may operate on their own action
items. ``guest`` callers don't see the tool in their menu.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.tasks.add_action_item")

# Stable kind prefix for LLM-driven action items. Each row
# gets a unique per-row suffix (``_<8-hex>``) so multiple
# open action items per operator don't collide with the
# partial unique index ``ux_action_items_open_per_kind``
# (which enforces one OPEN row per ``(uid, kind)``
# for stable system kinds like ``llm_credentials_missing``).
# ``list_action_item`` filters by
# ``kind LIKE 'llm_action_item_%'``.
_LLM_ACTION_ITEM_KIND_PREFIX = "llm_action_item"


def _new_llm_action_item_kind() -> str:
    return f"{_LLM_ACTION_ITEM_KIND_PREFIX}_{uuid.uuid4().hex[:8]}"


class AddActionItemTool(Tool):
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
        "target_url (optional in-app link). Each call creates one "
        "row; close with complete_action_item."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "What to do, ≤200 chars. The "
                    "operator-visible label."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Optional detail, ≤1000 chars. "
                    "Surfaces under the title in the "
                    "dashboard."
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
        },
        "required": ["title"],
    }

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        title = (kwargs.get("title") or "").strip()
        if not title:
            return ToolResult(
                content="title is required and must be non-empty",
                is_error=True,
            )
        if len(title) > 200:
            return ToolResult(
                content=f"title is too long ({len(title)} > 200)",
                is_error=True,
            )
        description = kwargs.get("description")
        if description is not None and len(description) > 1000:
            return ToolResult(
                content=(
                    f"description is too long "
                    f"({len(description)} > 1000)"
                ),
                is_error=True,
            )
        priority = kwargs.get("priority") or "normal"
        if priority not in ("normal", "high"):
            return ToolResult(
                content=(
                    f"priority must be 'normal' or 'high', "
                    f"got {priority!r}"
                ),
                is_error=True,
            )
        target_url = kwargs.get("target_url")
        if target_url is not None and len(target_url) > 500:
            return ToolResult(
                content=(
                    f"target_url is too long "
                    f"({len(target_url)} > 500)"
                ),
                is_error=True,
            )

        due_date: datetime | None = None
        raw_due = kwargs.get("due_date")
        if raw_due is not None and str(raw_due).strip():
            raw = str(raw_due).strip()
            # Accept YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS] as
            # lenient date parsing. We don't validate
            # calendar correctness — the ORM column is
            # nullable; a malformed date that parses to
            # NaN will be silently set to None.
            try:
                # Try ISO datetime first
                due_date = datetime.fromisoformat(raw)
            except ValueError:
                try:
                    # Fallback: date-only
                    due_date = datetime.strptime(raw, "%Y-%m-%d")
                except ValueError:
                    return ToolResult(
                        content=(
                            f"due_date must be a valid date "
                            f"(YYYY-MM-DD), got {raw!r}"
                        ),
                        is_error=True,
                    )

        item = ctx.bus.action_items_book.create_llm(
            uid=int(ctx.uid), kind=_new_llm_action_item_kind(), title=title,
            description=description, target_url=target_url, priority=priority, due_date=due_date,
        )
        logger.info(
            "add_action_item: item %s created for contact=%s title=%r",
            item.id, ctx.uid, title,
        )
        body = json.dumps({"created": item.to_dict()}, indent=2, ensure_ascii=False)
        # 8 KB matches the LLM-side truncation budget in
        # ``ToolResult`` (``base.ToolResult`` docstring) —
        # a chat turn shouldn't return a multi-KB action-item
        # list when the operator can just look at the dashboard.
        if len(body) > 8 * 1024:
            body = body[: 8 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)