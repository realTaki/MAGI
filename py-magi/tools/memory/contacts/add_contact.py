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
from tools.base import Tool, ToolResult

logger = logging.getLogger("tools.memory.add_contact")


class AddContactTool(Tool):
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

    @Tool.require_bus
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
        try:
            record_id = self.bus.contacts_book.add(Contact(
                name=name,
                display_name=kwargs.get("display_name"),
                role=role,
                tgid=kwargs.get("tgid"),
            ))
            contact = self.bus.contacts_book.get(record_id)
            if contact is None:
                raise RuntimeError(f"contact row {record_id} disappeared after insert")
        except ValueError as e:
            # ``contacts_book.add`` owns write invariants
            # (non-empty name, role enum membership).
            # Translate to a clean LLM-facing
            # error rather than letting it bubble to the
            # worker's "tool.crashed" envelope (which would
            # imply a programming error rather than a
            # caller-fixable validation).
            return ToolResult.err(str(e))

        initial_note = kwargs.get("notes")
        if initial_note and str(initial_note).strip():
            # Forwarded to a second Book so the contact row
            # and its first note live on the same schema
            # they would have had via a follow-up
            # ``add_contact_note`` call. We collapse both
            # outcomes into one ``{"created": ...,
            # "initial_note": ...}`` payload so the LLM
            # doesn't have to thread two tool results.
            try:
                note_id = self.bus.contact_notes_book.add(ContactNote(
                    contact_id=contact.id,
                    note=str(initial_note),
                ))
                note = self.bus.contact_notes_book.get(note_id)
                if note is None:
                    raise RuntimeError(f"contact note {note_id} disappeared after insert")
            except ValueError as e:
                # Contact row was created — surface the
                # note-validation failure but keep the
                # partial-success contact in the payload so
                # the LLM can decide whether to retry the
                # note write.
                logger.warning(
                    "add_contact: contact %s created but initial note rejected: %s",
                    contact.id,
                    e,
                )
                return ToolResult.ok(
                    {
                        "created": contact.to_dict(),
                        "initial_note": None,
                        "initial_note_error": str(e),
                    }
                )
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
