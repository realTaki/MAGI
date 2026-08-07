"""``list_action_item`` tool — return the calling
operator's *own* action items.

Strict per-contact privacy: a tool call from operator A
never sees operator B's rows, even if the LLM asks for an
id it doesn't own — the row is missing rather than shared.

Scope (per-contact, role-gated): only ``admin`` (per
:attr:`ctx.bus.magis_admins_book`) and ``assigned`` (per
``Contact.role``) operators may list their own action
items. ``guest`` callers don't see the tool in their menu.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.tasks.list_action_item")


class ListActionItemTool(Tool):
    """Return the calling operator's own action items."""

    name = "list_action_item"
    description = (
        "List the calling operator's action items. "
        "Use when the operator says '我还有哪些 "
        "todo' / '列出待办' / 'what's still open?' "
        "Inputs: include_completed (bool, default "
        "false — open action items only). Strict "
        "per-contact: only rows owned by the caller "
        "are returned. The operator's "
        "``llm_credentials_missing`` system row also "
        "appears here so the operator can see "
        "everything they own in one place."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "include_completed": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, include items already "
                    "completed or dismissed in the last "
                    "7 days (matches the dashboard's "
                    "default mix)."
                ),
            },
        },
    }

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        ct_id = int(ctx.uid)
        include_completed = bool(kwargs.get("include_completed"))

        rows = ctx.bus.action_items_book.list_llm_for_owner(
            owner_uid=ct_id, include_completed=include_completed,
        )
        body = json.dumps(
            {
                "items": [row.to_dict() for row in rows],
                "total": len(rows),
            },
            indent=2,
            ensure_ascii=False,
        )
        if len(body) > 8 * 1024:
            body = body[: 8 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)