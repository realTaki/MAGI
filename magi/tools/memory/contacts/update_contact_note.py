"""``update_contact_note`` tool — edit an existing note by
id.

Use when the operator says '改一下那条 / 把 ... 改成 ...'.
The ``note_id`` is visible in the ``add_contact_note``
result and the ``search_contacts`` output.

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

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.update_contact_note")


class UpdateContactNoteTool(Tool):
    """Edit an existing note by id."""

    name = "update_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Update an existing contact note by id. Use when the "
        "operator says '改一下那条 / 把 ... 改成 ...'. The note_id "
        "is visible in the add_contact_note result and the "
        "search_contacts output."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "id of the note row."},
            "note": {"type": "string", "description": "Replacement text."},
        },
        "required": ["note_id", "note"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        note = kwargs.get("note")
        if not isinstance(note_id, int):
            return ToolResult(
                content=f"note_id must be int, got {type(note_id).__name__}",
                is_error=True,
            )
        if not isinstance(note, str) or not note.strip():
            return ToolResult(
                content="note is required (non-empty string)",
                is_error=True,
            )
        try:
            bus = ctx.bus
            view = bus.contacts_book.update_note(note_id, note)
        except LookupError as e:
            return ToolResult(content=str(e), is_error=True)
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)