"""``complete_action_item`` tool — close an existing open
action item by id.

Idempotent: re-calling on an already-completed row returns
the existing row (same convention as
``/api/action_items/{id}/complete``). Strict per-contact
privacy: a tool call from operator A never sees operator B's
rows, even if the LLM asks for an id it doesn't own — the
row is missing rather than shared.

Scope (per-contact, role-gated): only ``admin`` (per
:attr:`ctx.bus.magis_admins_book`) and ``assigned`` (per
``Contact.role``) operators may operate on their own action
items. ``guest`` callers don't see the tool in their menu.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.tasks.complete_action_item")


class CompleteActionItemTool(Tool):
    """Close an existing open action item by id."""

    name = "complete_action_item"
    description = (
        "Mark one of the calling operator's action items "
        "complete. Idempotent: re-calling on an "
        "already-completed row returns the same "
        "state. Use when the operator says '做完 "
        "X 了' / 'close todo id=N' / '那条可以收 "
        "起来了'. Inputs: item_id (the action "
        "item's id; obtain it via list_action_item), "
        "note (optional ≤500 chars)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "integer",
                "description": (
                    "The action item's id. Only rows "
                    "owned by the calling operator "
                    "are completable — passing another "
                    "operator's id returns "
                    "is_error=True without leaking "
                    "existence (strict per-contact "
                    "privacy)."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional completion note (≤500 "
                    "chars). Surfaced in the "
                    "dashboard's 'recently completed' "
                    "list."
                ),
            },
        },
        "required": ["item_id"],
    }

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        raw_id = kwargs.get("item_id")
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            return ToolResult(
                content=f"item_id must be an integer, got {raw_id!r}",
                is_error=True,
            )
        note = kwargs.get("note")
        if note is not None and len(note) > 500:
            return ToolResult(
                content=f"note is too long ({len(note)} > 500)",
                is_error=True,
            )

        ct_id = int(ctx.uid)
        row = ctx.bus.action_items_book.complete_for_owner(
            action_item_id=item_id, owner_uid=ct_id, note=note,
        )
        if row is None:
            return ToolResult(
                content=(
                    f"action item {item_id} not found or "
                    f"not owned by the calling operator"
                ),
                is_error=True,
            )
        logger.info("complete_action_item: item %s completed by %s", item_id, ct_id)
        body = json.dumps({"item": row.to_dict()}, indent=2, ensure_ascii=False)
        if len(body) > 8 * 1024:
            body = body[: 8 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)