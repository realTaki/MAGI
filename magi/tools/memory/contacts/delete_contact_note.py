"""``delete_contact_note`` tool — remove a contact note by
id. Idempotent (deleting a non-existent id is a no-op
success). Use when the operator says '忘了那条 / 删掉'.

Tool author gate. ``role`` only carries ``assigned`` /
``guest`` — there is no ``role='admin'`` value. Admin is
a MAGIS-level concept, resolved at runtime via
:attr:`ctx.bus.magis_admins_book` (see
:meth:`magi.tools.base.Tool.gate`). The
``ALLOWED_ROLES = {"admin", "assigned"}`` whitelist
admits callers whose effective role-tag set intersects
it — ``admin`` from a MAGIS admin row, ``assigned``
from the contact's local role.

Bus plumbing: this tool talks to bus
(:class:`magi.bus.Bus`) via ``ctx.bus.contact_notes_book``
— the Book owns the data write and returns ``True`` if a
row was removed, ``False`` if no row matched (the same
``existed`` flag the BUS's ``ContactsService.delete_note``
exposed). The BUS service is no longer imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.delete_contact_note")


class DeleteContactNoteTool(Tool):
    """Remove a contact note by id. Idempotent."""

    name = "delete_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Delete a contact note by id. Idempotent — "
        "deleting a non-existent id is a no-op success. "
        "Use when the operator says '忘了那条 / 删掉'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {
                "type": "integer",
                "description": "id of the note row to remove.",
            },
        },
        "required": ["note_id"],
    }

    @Tool.require_bus
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        if not isinstance(note_id, int):
            return ToolResult.err(
                f"note_id must be int, got {type(note_id).__name__}"
            )

        existed = ctx.bus.contact_notes_book.delete_note(
            note_id=note_id,
        )
        logger.info(
            "delete_contact_note: note=%s existed=%s",
            note_id, existed,
        )
        return ToolResult.ok({"note_id": note_id, "existed": existed})
