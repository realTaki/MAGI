"""``delete_contact_note`` — remove a ContactNote.

Jobs:
  GetContactNoteJob — tell the caller whether the id existed.
  DeleteContactNoteJob — delete the row; missing id is still success.
"""

from __future__ import annotations

import logging
from typing import Any

from bus import DeleteContactNoteJob, GetContactNoteJob
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.delete_contact_note")

_PUBLISHER = "tools"


class DeleteContactNoteTool(BaseTool):
    """Remove a contact note by id. Idempotent."""

    name = "delete_contact_note"
    description = (
        "Delete a contact note by id. Idempotent — deleting a missing "
        "id is a no-op success."
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
    async def run(self, **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        if not isinstance(note_id, int):
            return ToolResult.err(f"note_id must be int, got {type(note_id).__name__}")
        existing = await self.publish(
            GetContactNoteJob(publisher=_PUBLISHER, contact_note_id=note_id)
        )
        if existing is None:
            return ToolResult.err("contact note book is not available")
        existed = existing.contact_note is not None
        deleted = await self.publish(
            DeleteContactNoteJob(publisher=_PUBLISHER, contact_note_id=note_id)
        )
        if deleted is None:
            return ToolResult.err("contact note book is not available")
        logger.info("delete_contact_note: note=%s existed=%s", note_id, existed)
        return ToolResult.ok({"note_id": note_id, "existed": existed})
