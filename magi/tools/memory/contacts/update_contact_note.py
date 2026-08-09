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

Bus plumbing: this tool talks to bus
(:class:`magi.bus.Bus`) via ``ctx.bus.contact_notes_book``
— the Book owns write invariants (non-empty note,
≤8 KB clamp) and exposes ``update_note(...)`` plus
``to_dict`` on the returned DTO. ``LookupError`` raised by
the Book for a missing row is translated to
``ToolResult.err`` so the LLM sees a caller-fixable
message rather than a worker "tool.crashed" envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.update_contact_note")


class UpdateContactNoteTool(Tool):
    """Edit an existing note by id."""

    name = "update_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Update an existing contact note by id. Use when "
        "the operator says '改一下那条 / 把 ... 改成 ...'. "
        "The note_id is visible in the add_contact_note "
        "result and the search_contacts output."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {
                "type": "integer",
                "description": "id of the note row.",
            },
            "note": {
                "type": "string",
                "description": (
                    "Replacement text. ≤8 KB; the Book "
                    "clamps whitespace and rejects empty."
                ),
            },
        },
        "required": ["note_id", "note"],
    }

    @Tool.require_bus
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        assert ctx.bus is not None, "require_bus should have caught this"
        note_id = kwargs.get("note_id")
        note = kwargs.get("note")
        if not isinstance(note_id, int):
            return ToolResult.err(
                f"note_id must be int, got {type(note_id).__name__}"
            )
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err(
                "note is required (non-empty string)"
            )

        try:
            row = ctx.bus.contact_notes_book.update_note(
                note_id=note_id, note=note,
            )
        except LookupError as e:
            # ``contact_notes_book.update_note`` raises
            # ``LookupError`` when ``note_id`` does not
            # resolve — same exception type
            # raised, so the LLM-facing error stays
            # caller-fixable rather than tripping the
            # worker's "tool.crashed" envelope.
            return ToolResult.err(str(e))
        except ValueError as e:
            # Write invariants (non-empty, length cap)
            # live on the Book.
            return ToolResult.err(str(e))

        logger.info(
            "update_contact_note: note=%s updated", row.id,
        )
        return ToolResult.ok({"updated": row.to_dict()})
