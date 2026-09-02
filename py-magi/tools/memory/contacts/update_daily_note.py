"""``update_daily_note`` — append to one DAILY note per contact.

Jobs:
  ListContactNotesJob — find the contact's current daily note (kind=daily).
  CreateContactNoteJob — first delta creates that daily row.
  UpdateContactNoteJob — later deltas append with a newline.
"""

from __future__ import annotations

import logging
from typing import Any

from bus import CreateContactNoteJob, ListContactNotesJob, NoteKind, UpdateContactNoteJob
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.update_daily_note")

_PUBLISHER = "tools"


class UpdateDailyNoteTool(BaseTool):
    """Append a delta to the contact's current daily note."""

    name = "update_daily_note"
    description = (
        "Append a delta to the calling contact's daily note. One daily "
        "note row per contact; later calls append with a newline. Use "
        "when something meaningful happened — task finished, preference "
        "shared, project context changed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "body_delta": {
                "type": "string",
                "description": "One short fact to append.",
            },
            "contact_id": {
                "type": "integer",
                "description": "Contact that owns the daily note.",
            },
        },
        "required": ["body_delta"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        body_delta = kwargs.get("body_delta")
        if not isinstance(body_delta, str) or not body_delta.strip():
            return ToolResult.err("body_delta is required (non-empty string)")
        try:
            contact_id = int(kwargs.get("contact_id") or 0)
        except (TypeError, ValueError):
            contact_id = 0
        if contact_id <= 0:
            return ToolResult.err("no contact_id on the calling context")
        listed = await self.publish(
            ListContactNotesJob(
                publisher=_PUBLISHER,
                contact_id=contact_id,
                kind=NoteKind.DAILY,
            )
        )
        if listed is None:
            return ToolResult.err("contact note book is not available")
        notes = listed.contact_notes or []
        delta = body_delta.strip()
        if not notes:
            created = await self.publish(
                CreateContactNoteJob(
                    publisher=_PUBLISHER,
                    contact_id=contact_id,
                    note=delta,
                    kind=NoteKind.DAILY,
                )
            )
            if created is None or created.contact_note_id is None:
                return ToolResult.err("contact note book is not available")
            logger.info(
                "update_daily_note: contact=%s created daily note=%s",
                contact_id,
                created.contact_note_id,
            )
            return ToolResult.ok({"contact_note_id": created.contact_note_id, "created": True})

        current = notes[0]
        merged = f"{(current.note or '').rstrip()}\n{delta}".strip()
        updated = await self.publish(
            UpdateContactNoteJob(
                publisher=_PUBLISHER,
                contact_note_id=current.id,
                note=merged,
                kind=NoteKind.DAILY,
            )
        )
        if updated is None:
            return ToolResult.err("contact note book is not available")
        logger.info("update_daily_note: contact=%s appended to note=%s", contact_id, current.id)
        return ToolResult.ok({"contact_note_id": current.id, "created": False})
