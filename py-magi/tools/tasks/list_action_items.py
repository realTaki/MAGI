"""``list_action_item`` tool — return the calling
operator's *own* action items.

Strict per-contact privacy: a tool call from operator A
never sees operator B's rows, even if the LLM asks for an
id it doesn't own — the row is missing rather than shared.

Scope (per-contact, role-gated): only ``admin`` (per
:attr:`self.bus.magis_admins_book`) and ``assigned`` (per
``Contact.role``) operators may list their own action
items. ``guest`` callers don't see the tool in their menu.
"""

from __future__ import annotations

import logging
from typing import Any

from old_bus.firmwares.books.local.actionItemBook import ActionSource
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.tasks.list_action_item")
COMPLETED_VISIBLE_DAYS = 7


class ListActionItemsTool(BaseTool):
    """Return user-authored action items for the calling operator.

    Scope: only ``source = 'user'`` rows are returned
    (filter applied via ``list_actions(..., source=ActionSource.USER)``).
    System-generated rows (``source = 'proactive'``, e.g. the
    credentials nudge) are excluded — the dashboard surfaces
    those separately, and the LLM-driven tool surface should
    not mix them into its menu.
    """

    name = "list_action_item"
    description = (
        "List the calling operator's user-authored action "
        "items (rows the operator added via add_action_item). "
        "Use when the operator says '我还有哪些 todo' / "
        "'列出待办' / 'what's still open?'. Inputs: "
        "include_completed (bool, default false — open rows "
        "only; true also surfaces rows completed/dismissed in "
        "the last 7 days). Strict per-contact: only rows "
        "owned by the caller are returned. System-generated "
        "rows (proactive nudges) are NOT included here — those "
        "live on the dashboard, not in the LLM tool menu."
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

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        ct_id = int(kwargs.get("contact_id") or 0)
        include_completed = bool(kwargs.get("include_completed"))

        rows = self.bus.action_items_book.list_actions(
            owner_contact_id=ct_id,
            include_completed=include_completed,
            source=ActionSource.USER,
            completed_visible_days=COMPLETED_VISIBLE_DAYS,
        )
        logger.info(
            "list_action_item: contact=%s include_completed=%s returned=%s",
            ct_id,
            include_completed,
            len(rows),
        )
        # ``ToolResult.ok`` handles JSON serialisation
        # (indent=2, ensure_ascii=False) and the 8 KB
        # truncation marker for free — same shape
        # ``add_action_item`` / ``complete_action_item``
        # use, no need to roll our own here.
        return ToolResult.ok(
            {
                "items": [row.to_dict() for row in rows],
                "total": len(rows),
            }
        )
