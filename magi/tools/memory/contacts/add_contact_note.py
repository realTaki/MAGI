"""``add_contact_note`` tool — append one new note row to
a contact.

Notes are individual ``contact_notes`` rows; the LLM can
update or delete by id without rewriting anything else
about the same person.

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
— the Book owns write invariants (non-empty note,
≤8 KB clamp) and exposes ``add(...)`` plus
``to_dict`` on the returned DTO. The legacy service at
bus Book API is no
longer imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.add_contact_note")


class AddContactNoteTool(Tool):
    """Append one new note row to a contact."""

    name = "add_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Add a new note about an existing contact (by uid). "
        "Each call creates one row in contact_notes — "
        "keep each note to one fact (≤8 KB). To update or "
        "delete an existing note, use update_contact_note / "
        "delete_contact_note with the note_id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "uid": {
                "type": "integer",
                "description": "Contact uid (required).",
            },
            "note": {
                "type": "string",
                "description": (
                    "One short fact. ≤8 KB; the Book "
                    "clamps whitespace and rejects empty."
                ),
            },
        },
        "required": ["uid", "note"],
    }

    @Tool.require_bus
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        uid = kwargs.get("uid")
        note = kwargs.get("note")
        if not isinstance(uid, int):
            return ToolResult.err(
                f"uid must be int, got {type(uid).__name__}"
            )
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err(
                "note is required (non-empty string)"
            )

        # Pre-check the parent contact resolves — the FK
        # violation would otherwise surface as a SQLAlchemy
        # error caught at the outer worker layer (which
        # reads as "tool.crashed"). We translate to a clean
        # ``is_error=True`` here so the LLM sees a
        # caller-fixable "uid N not found" message.
        contact = ctx.bus.contacts_book.get(contact_id=uid)
        if contact is None:
            return ToolResult.err(f"contact {uid!r} not found")

        try:
            row = ctx.bus.contact_notes_book.add(
                contact_id=uid, note=note,
            )
        except ValueError as e:
            # ``contact_notes_book.add`` owns the
            # non-empty-after-strip and length-cap
            # invariants. Translate to LLM-facing error.
            return ToolResult.err(str(e))

        logger.info(
            "add_contact_note: note=%s appended to contact=%s",
            row.id, uid,
        )
        return ToolResult.ok({"created": row.to_dict()})
