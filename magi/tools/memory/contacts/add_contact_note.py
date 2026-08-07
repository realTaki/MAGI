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
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult, require_bus

logger = logging.getLogger("magi.tools.memory.add_contact_note")


class AddContactNoteTool(Tool):
    """Append one new note row to a contact."""

    name = "add_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Add a new note about an existing contact (by uid). "
        "Each call creates one row in contact_notes — keep "
        "each note to one fact. To update or delete an "
        "existing note, use update_contact_note / "
        "delete_contact_note with the note_id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "uid": {"type": "integer", "description": "Contact uid (required)."},
            "note": {"type": "string", "description": "One short fact (<=8 KB)."},
        },
        "required": ["uid", "note"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        uid = kwargs.get("uid")
        note = kwargs.get("note")
        if not isinstance(uid, int):
            return ToolResult(
                content=f"uid must be int, got {type(uid).__name__}",
                is_error=True,
            )
        if not isinstance(note, str) or not note.strip():
            return ToolResult(
                content="note is required (non-empty string)",
                is_error=True,
            )
        try:
            bus = ctx.bus
            view = bus.contacts_book.add_note(uid, note)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)