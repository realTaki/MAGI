"""``save_contact_note`` tool — create or edit a contact note.

``contact_id`` + ``note`` appends a row. ``note_id`` + ``note``
patches an existing row. Delete stays on ``delete_contact_note``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from old_bus.firmwares.books.local.contactBook import ContactNote
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.save_contact_note")


class SaveContactNoteTool(BaseTool):
    """Create or edit a contact note."""

    name = "save_contact_note"
    description = (
        "Add or update a note about a contact. "
        "Create: contact_id + note (one fact per row, ≤8 KB). "
        "Update: note_id + note. If both ids are present, note_id wins. "
        "To delete, use delete_contact_note. "
        "note_id is visible in the create result and search_contacts output."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "integer",
                "description": "Contact id. Required when creating a new note.",
            },
            "note_id": {
                "type": "integer",
                "description": "id of an existing note row to replace. Required when updating.",
            },
            "note": {
                "type": "string",
                "description": (
                    "One short fact, or the replacement text. "
                    "≤8 KB; the Book clamps whitespace and rejects empty."
                ),
            },
        },
        "required": ["note"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        if kwargs.get("note_id") is not None:
            return await self._update(**kwargs)
        return await self._create(**kwargs)

    async def _create(self, **kwargs: Any) -> ToolResult:
        contact_id = kwargs.get("contact_id")
        note = kwargs.get("note")
        if not isinstance(contact_id, int):
            return ToolResult.err(f"contact_id must be int, got {type(contact_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err("note is required (non-empty string)")

        contact = self.bus.contacts_book.get(contact_id)
        if contact is None:
            return ToolResult.err(f"contact {contact_id!r} not found")

        note_id = self.bus.contact_notes_book.add(ContactNote(
            contact_id=contact_id,
            note=note,
        ))
        row = self.bus.contact_notes_book.get(note_id)

        logger.info(
            "save_contact_note: note=%s appended to contact=%s",
            row.id,
            contact_id,
        )
        return ToolResult.ok({"created": row.to_dict()})

    async def _update(self, **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        note = kwargs.get("note")
        if not isinstance(note_id, int):
            return ToolResult.err(f"note_id must be int, got {type(note_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err("note is required (non-empty string)")

        book = self.bus.contact_notes_book
        existing = book.get(note_id)
        if existing is None:
            return ToolResult.err(f"contact_note {note_id!r} not found")
        book.update(replace(existing, note=note))
        row = book.get(note_id) or replace(existing, note=note)
        logger.info(
            "save_contact_note: note=%s updated",
            row.id,
        )
        return ToolResult.ok({"updated": row.to_dict()})
