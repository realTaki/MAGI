"""``update_daily_note`` tool — append a delta to today's
daily note for the caller.

The daily note is the running log the LLM appends to over
the course of a conversation — "I sent the Q3 invoice to
Lily", "Mark mentioned he's OOO Friday", "user prefers
shorter replies". The morning / night report reads
today's row verbatim; permanent ``add_contact_note``
rows stay separate.

Capture rules (full text lives in
``prompts/context/daily_note.md`` — folded into the system prompt):

- Record from the user (tasks done, preferences, project
  context). Don't record trivial external facts.
- Append only — never delete or rewrite prior deltas. The
  upsert appends with a newline separator; concurrent
  writes hit a unique-on-``(contact_id, kind, note_date)``
  index and serialize on the row update.

Catalog filter: ``ALLOWED_ROLES = {"admin", "assigned"}``.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.contact_notes_book``
— the Book owns the upsert + daily-append logic,
length cap, and ``note_date`` defaulting. Returns the
DTO so the LLM sees the post-write row. The bus
service at
bus Book API
is no longer imported here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.update_daily_note")


class UpdateDailyNoteTool(BaseTool):
    """Append a delta to today's daily note for the caller."""

    name = "update_daily_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Append a delta to today's daily note for the "
        "current operator (or the contact_id you pass). One row "
        "per (contact_id, day). Use when something meaningful "
        "happened — task finished, email sent, user "
        "shared a preference, project context changed. "
        "Don't write trivial external facts. The morning "
        "/ night report reads today's row verbatim."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "body_delta": {
                "type": "string",
                "description": (
                    "One short fact to append. ≤8 KB; the "
                    "Book strips whitespace and clamps to "
                    "the per-row 32 KB cap."
                ),
            },
            "note_date": {
                "type": "string",
                "description": (
                    "YYYY-MM-DD; defaults to today UTC. "
                    "Pass explicit only for back-filling a "
                    "missed day."
                ),
            },
        },
        "required": ["body_delta"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        body_delta = kwargs.get("body_delta")
        if not isinstance(body_delta, str) or not body_delta.strip():
            return ToolResult.err("body_delta is required (non-empty string)")
        # Default to the operator's own contact_id — the LLM
        # never specifies a different contact_id here (no
        # override on input_schema). Future cross-contact
        # notes should go through a separate
        # ``update_daily_note_for`` shape.
        contact_id = int(kwargs.get("contact_id") or 0)
        if contact_id is None or contact_id == 0:
            return ToolResult.err("no contact_id on the calling context")

        note_date: datetime | None = None
        raw_date = kwargs.get("note_date")
        if raw_date:
            try:
                note_date = datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                return ToolResult.err(f"note_date must be YYYY-MM-DD, got {raw_date!r}")

        row = self.bus.contact_notes_book.upsert_daily_note(
            contact_id=int(contact_id),
            body_delta=body_delta,
            note_date=note_date,
        )

        logger.info(
            "update_daily_note: contact=%s appended to row=%s",
            contact_id,
            row.id,
        )
        return ToolResult.ok({"updated": row.to_dict()})
