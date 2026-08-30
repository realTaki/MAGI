"""``add_contact_note`` tool — append one new note row to
a contact.

Notes are individual ``contact_notes`` rows; the LLM can
update or delete by id without rewriting anything else
about the same person.

Catalog filter: ``ALLOWED_ROLES = {"admin", "assigned"}``.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.contact_notes_book``
— the Book owns write invariants (non-empty note,
≤8 KB clamp) and exposes ``add(...)`` plus
``to_dict`` on the returned DTO. The legacy service at
bus Book API is no
longer imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from old_bus.firmwares.books.local.contactBook import ContactNote
from tools.base import Tool, ToolResult

logger = logging.getLogger("tools.memory.add_contact_note")


class AddContactNoteTool(Tool):
    """Append one new note row to a contact."""

    name = "add_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Add a new note about an existing contact (by contact_id). "
        "Each call creates one row in contact_notes — "
        "keep each note to one fact (≤8 KB). To update or "
        "delete an existing note, use update_contact_note / "
        "delete_contact_note with the note_id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "integer",
                "description": "Contact contact_id (required).",
            },
            "note": {
                "type": "string",
                "description": (
                    "One short fact. ≤8 KB; the Book clamps whitespace and rejects empty."
                ),
            },
        },
        "required": ["contact_id", "note"],
    }

    @Tool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        contact_id = kwargs.get("contact_id")
        note = kwargs.get("note")
        if not isinstance(contact_id, int):
            return ToolResult.err(f"contact_id must be int, got {type(contact_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err("note is required (non-empty string)")
        if self.bus is None:
            return ToolResult.err("bus not available")

        # Pre-check the parent contact resolves — the FK
        # violation would otherwise surface as a SQLAlchemy
        # error caught at the outer worker layer (which
        # reads as "tool.crashed"). We translate to a clean
        # ``is_error=True`` here so the LLM sees a
        # caller-fixable "contact_id N not found" message.
        contact = self.bus.contacts_book.get(contact_id)
        if contact is None:
            return ToolResult.err(f"contact {contact_id!r} not found")

        try:
            note_id = self.bus.contact_notes_book.add(ContactNote(
                contact_id=contact_id,
                note=note,
            ))
            row = self.bus.contact_notes_book.get(note_id)
            if row is None:
                raise RuntimeError(f"contact note {note_id} disappeared after insert")
        except ValueError as e:
            # ``contact_notes_book.add`` owns the
            # non-empty-after-strip and length-cap
            # invariants. Translate to LLM-facing error.
            return ToolResult.err(str(e))

        logger.info(
            "add_contact_note: note=%s appended to contact=%s",
            row.id,
            contact_id,
        )
        return ToolResult.ok({"created": row.to_dict()})
