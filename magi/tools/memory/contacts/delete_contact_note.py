"""``delete_contact_note`` tool — remove a contact note by
id. Idempotent (deleting a non-existent id is a no-op
success). Use when the operator says '忘了那条 / 删掉'.

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

logger = logging.getLogger("magi.tools.memory.delete_contact_note")


class DeleteContactNoteTool(Tool):
    """Remove a contact note by id. Idempotent."""

    name = "delete_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Delete a contact note by id. Idempotent — deleting a "
        "non-existent id is a no-op success. Use when the "
        "operator says '忘了那条 / 删掉'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "id of the note row to remove."},
        },
        "required": ["note_id"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        if not isinstance(note_id, int):
            return ToolResult(
                content=f"note_id must be int, got {type(note_id).__name__}",
                is_error=True,
            )
        existed = ctx.bus.contacts_book.delete_note(note_id)
        body = json.dumps(
            {"note_id": note_id, "existed": existed},
            indent=2,
            ensure_ascii=False,
        )
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)