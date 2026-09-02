"""``save_contact_note`` tool — create or edit a note through Jobs."""

from __future__ import annotations

import logging
from typing import Any

from bus import (
    CreateContactNoteJob,
    GetContactJob,
    GetContactNoteJob,
    NoteKind,
    UpdateContactNoteJob,
)
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.save_contact_note")

_PUBLISHER = "tools"


class SaveContactNoteTool(BaseTool):
    """Create or edit a contact note."""

    name = "save_contact_note"
    description = (
        "Add or update a note about a contact. "
        "Create: contact_id + note. Update: note_id + note. "
        "If both ids are present, note_id wins. "
        "To delete, use delete_contact_note."
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
                "description": "id of an existing note row to replace.",
            },
            "note": {
                "type": "string",
                "description": "One short fact, or the replacement text.",
            },
            "kind": {
                "type": "string",
                "enum": [kind.value for kind in NoteKind],
                "description": "permanent | daily. Defaults to permanent on create.",
            },
        },
        "required": ["note"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        if kwargs.get("note_id") is not None:
            return await self._update(**kwargs)
        return await self._create(**kwargs)

    async def _kind(self, raw: Any, *, default: NoteKind | None) -> NoteKind | ToolResult | None:
        if raw is None:
            return default
        try:
            return NoteKind(raw)
        except ValueError:
            return ToolResult.err("kind must be permanent or daily")

    async def _create(self, **kwargs: Any) -> ToolResult:
        contact_id = kwargs.get("contact_id")
        note = kwargs.get("note")
        if not isinstance(contact_id, int):
            return ToolResult.err(f"contact_id must be int, got {type(contact_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err("note is required (non-empty string)")
        kind = await self._kind(kwargs.get("kind"), default=NoteKind.PERMANENT)
        if isinstance(kind, ToolResult):
            return kind
        found = await self.publish(GetContactJob(publisher=_PUBLISHER, contact_id=contact_id))
        if found is None:
            return ToolResult.err("contact book is not available")
        if found.contact is None:
            return ToolResult.err(f"contact {contact_id} not found")
        created = await self.publish(
            CreateContactNoteJob(
                publisher=_PUBLISHER,
                contact_id=contact_id,
                note=note.strip(),
                kind=kind,
            )
        )
        if created is None or created.contact_note_id is None:
            return ToolResult.err("contact note book is not available")
        fetched = await self.publish(
            GetContactNoteJob(publisher=_PUBLISHER, contact_note_id=created.contact_note_id)
        )
        row = None if fetched is None else fetched.contact_note
        logger.info("save_contact_note: note=%s appended to contact=%s", created.contact_note_id, contact_id)
        return ToolResult.ok({"created": None if row is None else row.to_dict()})

    async def _update(self, **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        note = kwargs.get("note")
        if not isinstance(note_id, int):
            return ToolResult.err(f"note_id must be int, got {type(note_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err("note is required (non-empty string)")
        kind = await self._kind(kwargs.get("kind"), default=None)
        if isinstance(kind, ToolResult):
            return kind
        existing = await self.publish(
            GetContactNoteJob(publisher=_PUBLISHER, contact_note_id=note_id)
        )
        if existing is None:
            return ToolResult.err("contact note book is not available")
        if existing.contact_note is None:
            return ToolResult.err(f"contact_note {note_id} not found")
        updated = await self.publish(
            UpdateContactNoteJob(
                publisher=_PUBLISHER,
                contact_note_id=note_id,
                note=note.strip(),
                kind=kind,
            )
        )
        if updated is None:
            return ToolResult.err("contact note book is not available")
        fetched = await self.publish(
            GetContactNoteJob(publisher=_PUBLISHER, contact_note_id=note_id)
        )
        row = None if fetched is None else fetched.contact_note
        logger.info("save_contact_note: note=%s updated", note_id)
        return ToolResult.ok({"updated": None if row is None else row.to_dict()})
