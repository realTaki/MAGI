"""``update_contact_note`` tool — edit an existing note by
id.

Use when the operator says '改一下那条 / 把 ... 改成 ...'.
The ``note_id`` is visible in the ``add_contact_note``
result and the ``search_contacts`` output.

Catalog filter: ``ALLOWED_ROLES = {"admin", "assigned"}``.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.contact_notes_book``.
The flow is ``get`` → ``with_changes`` → base ``update``;
a missing row is rendered as ``ToolResult.err`` so the
LLM sees a caller-fixable message rather than a worker
"tool.crashed" envelope. The updated DTO is returned
via ``to_dict`` for the JSON transport.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.update_contact_note")


class UpdateContactNoteTool(BaseTool):
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
                    "Replacement text. ≤8 KB; the Book clamps whitespace and rejects empty."
                ),
            },
        },
        "required": ["note_id", "note"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        note_id = kwargs.get("note_id")
        note = kwargs.get("note")
        if not isinstance(note_id, int):
            return ToolResult.err(f"note_id must be int, got {type(note_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return ToolResult.err("note is required (non-empty string)")

        book = self.bus.contact_notes_book
        existing = book.get(note_id)
        if existing is None:
            # Base ``update`` would have returned ``False``
            # for this id; we pre-check so the LLM sees a
            # caller-fixable ``ToolResult.err`` rather than
            # tripping the worker's "tool.crashed" envelope.
            return ToolResult.err(f"contact_note {note_id!r} not found")
        book.update(replace(existing, note=note))
        # Re-read so ``updated_at`` matches the row the DB stored;
        # fall back to the candidate we just wrote in the unlikely
        # race where the row vanished between update and read.
        row = book.get(note_id) or replace(existing, note=note)
        logger.info(
            "update_contact_note: note=%s updated",
            row.id,
        )
        return ToolResult.ok({"updated": row.to_dict()})
