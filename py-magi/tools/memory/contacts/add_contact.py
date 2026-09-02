"""``add_contact`` tool — create a new contact in the
directory.

Notes about an existing contact are recorded separately
via :mod:`tools.memory.contacts.add_contact_note`; this
tool accepts an optional ``notes`` argument as a convenience
for "create + first observation" flows and forwards it to
:mod:`tools.memory.contacts.add_contact_note` so both
paths land on the same ``contact_notes`` row shape.

Catalog filter: ``ALLOWED_ROLES = {"admin", "assigned"}``.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.contacts_book``
and ``self.bus.contact_notes_book`` — the Books own
write invariants (length caps,
empty-content rejection) and expose ``add(...)`` plus
``to_dict`` on the returned DTO. The legacy service at
bus Book API is no longer
imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from old_bus.firmwares.books.local.contactBook import Contact, ContactNote, Role
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.add_contact")


class AddContactTool(BaseTool):
    """Create a new contact."""

    name = "add_contact"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Create a new contact (person) in the directory. "
        "Name is required. display_name, tgid, "
        "role ('assigned' default / 'guest'), and "
        "notes (initial note, optional) are optional. "
        "To add notes about an existing contact, use "
        "add_contact_note instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Contact name (required, ≤120 chars)."
                ),
            },
            "display_name": {
                "type": "string",
                "description": "Display name (optional).",
            },
            "tgid": {
                "type": "integer",
                "description": "Telegram user id (optional).",
            },
            "role": {
                "type": "string",
                "enum": ["assigned", "guest"],
                "default": "guest",
                "description": (
                    "MAGI-local role tag. ``assigned`` "
                    "marks the operator this MAGI serves; "
                    "``guest`` (default) marks everyone else. "
                    "Admin lives on the separate MAGIS table, "
                    "not here."
                ),
            },
            "notes": {
                "type": "string",
                "description": (
                    "Initial note (optional, ≤8 KB). Forwarded "
                    "to add_contact_note after the contact row "
                    "is created — same shape as a permanent "
                    "fact, just bundled for convenience."
                ),
            },
        },
        "required": ["name"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        name = kwargs.get("name")
        if not isinstance(name, str) or not name.strip():
            return ToolResult.err("name is required (non-empty string)")
        role_str = kwargs.get("role") or "guest"
        try:
            role = Role(role_str)
        except ValueError:
            return ToolResult.err(
                f"role must be one of {sorted(r.value for r in Role)!r}, got {role_str!r}"
            )
        record_id = self.bus.contacts_book.add(Contact(
            name=name,
            display_name=kwargs.get("display_name"),
            role=role,
            tgid=kwargs.get("tgid"),
        ))
        contact = self.bus.contacts_book.get(record_id)

        initial_note = kwargs.get("notes")
        if initial_note and str(initial_note).strip():
            note_id = self.bus.contact_notes_book.add(ContactNote(
                contact_id=contact.id,
                note=str(initial_note),
            ))
            note = self.bus.contact_notes_book.get(note_id)
            logger.info(
                "add_contact: contact=%s created with initial note=%s",
                contact.id,
                note.id,
            )
            return ToolResult.ok(
                {
                    "created": contact.to_dict(),
                    "initial_note": note.to_dict(),
                }
            )

        logger.info("add_contact: contact=%s created", contact.id)
        return ToolResult.ok({"created": contact.to_dict()})
