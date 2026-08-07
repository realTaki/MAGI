"""LLM-callable contact tools.

Write:
  - :class:`AddContactTool`       — create a new contact.
  - :class:`AddContactNoteTool`   — add a note about a contact.
  - :class:`UpdateContactNoteTool` — update a specific note by id.
  - :class:`DeleteContactNoteTool` — remove a specific note.
  - :class:`UpdateDailyNoteTool`  — append a delta to today's daily note.
Read:
  - :class:`SearchContactsTool`   — search contacts + notes.

Notes are individual rows in ``contact_notes`` — each
call to ``add_contact_note`` creates one row.  The agent
can update or delete individual notes by id without
rewriting everything else about the same person.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
)


logger = logging.getLogger("magi.tools.memory.contacts")

# Tool author gate. After the 2024 role/admin split,
# ``role='admin'`` is no longer a reachable value — the
# ``admin`` boolean carries WebUI access rights instead.
# The gate (``admin=True OR role='assigned'``) is now
# centralized in :meth:`Tool.check_gate`, which reads
# ``ctx.bus.contacts_book`` to resolve both the role and
# the admin flag.  Per-tool ``_gate()`` helpers are gone.


def _err(msg: str) -> ToolResult:
    return ToolResult(content=msg, is_error=True)


def _ok(payload: Any) -> ToolResult:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(body) > 4 * 1024:
        body = body[: 4 * 1024] + "\n…(truncated)"
    return ToolResult(content=body, is_error=False)


# -- AddContactTool -----------------------------------------------------------


class AddContactTool(Tool):
    """Create a new contact."""

    name = "add_contact"
    ALLOWED_ROLES = frozenset({"assigned"})
    description = (
        "Create a new contact (person) in the directory. "
        "Name is required. display_name, telegram_id, and "
        "notes (initial note) are optional. "
        "To add notes about an existing contact, use "
        "add_contact_note instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Contact name (required, unique)."},
            "display_name": {"type": "string", "description": "Display name (optional)."},
            "telegram_id": {"type": "integer", "description": "Telegram user id (optional)."},
            "notes": {"type": "string", "description": "Initial note (optional)."},
            "role": {"type": "string", "description": "assigned/guest. Default 'guest'."},
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        name = kwargs.get("name")
        if not isinstance(name, str) or not name.strip():
            return _err("name is required (non-empty string)")
        try:
            bus = ctx.bus
            view = bus.contacts_book.create_contact(
                name=name,
                display_name=kwargs.get("display_name"),
                role=kwargs.get("role") or "guest",
                telegram_id=kwargs.get("telegram_id"),
                notes=kwargs.get("notes") or "",
            )
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- UpdateDailyNoteTool -----------------------------------------------------


class UpdateDailyNoteTool(Tool):
    """Append a delta to today's daily note for the caller.

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
    """

    name = "update_daily_note"
    ALLOWED_ROLES = frozenset({"assigned"})
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
            return _err("body_delta is required (non-empty string)")
        # Default to the operator's own uid — the LLM never
        # specifies a different uid here (no override on
        # input_schema). Future cross-contact notes should go
        # through a separate ``update_daily_note_for`` shape.
        contact_id = ctx.uid
        if contact_id is None or contact_id == 0:
            return _err("no uid on the calling context")

        from datetime import datetime as _dt
        note_date: Any = None
        raw_date = kwargs.get("note_date")
        if raw_date:
            try:
                note_date = _dt.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                return _err(f"note_date must be YYYY-MM-DD, got {raw_date!r}")

        try:
            bus = ctx.bus
            view = bus.contacts_book.upsert_daily_note(
                int(contact_id), body_delta, note_date=note_date
            )
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- AddContactNoteTool ------------------------------------------------------


class AddContactNoteTool(Tool):
    """Append one new note row to a contact.

    Notes are individual ``contact_notes`` rows; the LLM can
    update or delete by id without rewriting anything else
    about the same person.
    """

    name = "add_contact_note"
    ALLOWED_ROLES = frozenset({"assigned"})
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
            return _err(f"uid must be int, got {type(uid).__name__}")
        if not isinstance(note, str) or not note.strip():
            return _err("note is required (non-empty string)")
        try:
            bus = ctx.bus
            view = bus.contacts_book.add_note(uid, note)
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- UpdateContactNoteTool ---------------------------------------------------


class UpdateContactNoteTool(Tool):
    """Edit an existing note by id."""

    name = "update_contact_note"
    ALLOWED_ROLES = frozenset({"assigned"})
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
            return _err(f"note_id must be int, got {type(note_id).__name__}")
        if not isinstance(note, str) or not note.strip():
            return _err("note is required (non-empty string)")
        try:
            bus = ctx.bus
            view = bus.contacts_book.update_note(note_id, note)
        except LookupError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- DeleteContactNoteTool ---------------------------------------------------


class DeleteContactNoteTool(Tool):
    """Remove a contact note by id. Idempotent."""

    name = "delete_contact_note"
    ALLOWED_ROLES = frozenset({"assigned"})
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
            return _err(f"note_id must be int, got {type(note_id).__name__}")
        existed = ctx.bus.contacts_book.delete_note(note_id)
        return _ok({"note_id": note_id, "existed": existed})


# -- SearchContactsTool ------------------------------------------------------


class SearchContactsTool(Tool):
    """Search contacts by name or by note content."""

    name = "search_contacts"
    ALLOWED_ROLES = frozenset({"assigned"})
    description = (
        "Search the contact directory by name or by note text. "
        "Returns the matching contacts and a sample of their "
        "notes. Use when the operator says '查一下 Lily / 谁在财务部'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search string (matches name or note text, case-insensitive substring).",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Max contacts to return. Default 20.",
            },
        },
        "required": ["query"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        limit = kwargs.get("limit") or 20
        if not isinstance(query, str) or not query.strip():
            return _err("query is required (non-empty string)")
        bus = ctx.bus
        views = bus.contacts_book.search(query, limit=limit)
        return _ok({
            "query": query,
            "count": len(views),
            "contacts": [v.to_dict() for v in views],
        })


__all__ = [
    "AddContactTool",
    "AddContactNoteTool",
    "UpdateContactNoteTool",
    "DeleteContactNoteTool",
    "UpdateDailyNoteTool",
    "SearchContactsTool",
]
