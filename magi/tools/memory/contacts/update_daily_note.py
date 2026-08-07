"""``update_daily_note`` tool — append a delta to today's
daily note for the caller.

The daily note is the running log the LLM appends to over
the course of a conversation — "I sent the Q3 invoice to
Lily", "Mark mentioned he's OOO Friday", "user prefers
shorter replies". The morning / night report reads
today's row verbatim; permanent ``add_contact_note``
rows stay separate.

Capture rules (full text lives in
``prompts/context/daily_note.md`` — folded into the system prompt
only when the operator toggles ``system.show_daily_note_prompt``):

- Record from the user (tasks done, preferences, project
  context). Don't record trivial external facts.
- Append only — never delete or rewrite prior deltas. The
  upsert appends with a newline separator; concurrent
  writes hit the partial unique index ``ux_contact_notes_daily``
  and serialize on the row update.

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
from datetime import datetime
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.update_daily_note")


class UpdateDailyNoteTool(Tool):
    """Append a delta to today's daily note for the caller."""

    name = "update_daily_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Append a delta to today's daily note for the current "
        "operator (or the uid you pass). One row per "
        "(uid, day). Use when something meaningful happened — "
        "task finished, email sent, user shared a preference, "
        "project context changed. Don't write trivial external "
        "facts. The morning / night report reads today's row "
        "verbatim."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "body_delta": {
                "type": "string",
                "description": (
                    "One short fact to append. <=8 KB. The tool "
                    "strips whitespace and clamps to the per-row "
                    "32 KB cap."
                ),
            },
            "note_date": {
                "type": "string",
                "description": (
                    "YYYY-MM-DD; defaults to today UTC. Pass "
                    "explicit only for back-filling a missed day."
                ),
            },
        },
        "required": ["body_delta"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        body_delta = kwargs.get("body_delta")
        if not isinstance(body_delta, str) or not body_delta.strip():
            return ToolResult(
                content="body_delta is required (non-empty string)",
                is_error=True,
            )
        # Default to the operator's own uid — the LLM never
        # specifies a different uid here (no override on
        # input_schema). Future cross-contact notes should go
        # through a separate ``update_daily_note_for`` shape.
        contact_id = ctx.uid
        if contact_id is None or contact_id == 0:
            return ToolResult(
                content="no uid on the calling context",
                is_error=True,
            )

        note_date: datetime | None = None
        raw_date = kwargs.get("note_date")
        if raw_date:
            try:
                note_date = datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                return ToolResult(
                    content=f"note_date must be YYYY-MM-DD, got {raw_date!r}",
                    is_error=True,
                )

        try:
            bus = ctx.bus
            view = bus.contacts_book.upsert_daily_note(
                int(contact_id), body_delta, note_date=note_date
            )
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)