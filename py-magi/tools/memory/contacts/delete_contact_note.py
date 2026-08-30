"""``delete_contact_note`` tool — remove a contact note by
id. Idempotent (deleting a non-existent id is a no-op
success). Use when the operator says '忘了那条 / 删掉'.

Catalog filter: ``ALLOWED_ROLES = {"admin", "assigned"}``.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.contact_notes_book``
— the Book owns the data write and returns ``True`` if a
row was removed, ``False`` if no row matched (the same
``existed`` flag the bus's ``ContactsService.delete_note``
exposed). The old service is no longer imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.delete_contact_note")


class DeleteContactNoteTool(BaseTool):
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

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        if not isinstance(note_id, int):
            return ToolResult.err(f"note_id must be int, got {type(note_id).__name__}")

        existed = self.bus.contact_notes_book.delete(note_id)
        logger.info(
            "delete_contact_note: note=%s existed=%s",
            note_id,
            existed,
        )
        return ToolResult.ok({"note_id": note_id, "existed": existed})
