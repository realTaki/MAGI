"""``complete_action_item`` tool — close an existing open
action item by id.

Idempotent: re-calling on an already-completed row returns
the existing row (same convention as
``/api/action_items/{id}/complete``). Strict per-contact
privacy: a tool call from operator A never sees operator B's
rows, even if the LLM asks for an id it doesn't own — the
row is missing rather than shared.

Scope (per-contact, role-gated): only ``admin`` (per
:attr:`self.bus.magis_admins_book`) and ``assigned`` (per
``Contact.role``) operators may operate on their own action
items. ``guest`` callers don't see the tool in their menu.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.tasks.complete_action_item")


class CompleteActionItemTool(BaseTool):
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

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        raw_id = kwargs.get("item_id")
        if raw_id is None:
            return ToolResult.err("item_id is required")
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            return ToolResult.err(f"item_id must be an integer, got {raw_id!r}")
        note = kwargs.get("note")
        # ``note`` length is enforced by
        # :meth:`ActionItemBook.complete` — we don't
        # re-check here.

        ct_id = int(kwargs.get("contact_id") or 0)
        # Auth lives at the tool layer, not in the Book:
        # ``ActionItemBook.complete`` is a pure data write.
        # Strict per-contact privacy is enforced here by a
        # ``get`` followed by a ``row.contact_id == caller`` check
        # before any write fires. The TOCTOU window between
        # ``get`` and ``complete`` is fine for the
        # single-writer chat tool; a future tx-scoped guard
        # would tighten it for multi-writer surfaces.
        existing = self.bus.action_items_book.get(item_id)
        if existing is None or existing.contact_id != ct_id:
            return ToolResult.err(
                f"action item {item_id} not found or not owned by the calling operator"
            )
        row = self.bus.action_items_book.complete(
            action_item_id=item_id,
            note=note,
        )
        logger.info("complete_action_item: item %s completed by %s", item_id, ct_id)
        return ToolResult.ok({"item": row.to_dict()})
