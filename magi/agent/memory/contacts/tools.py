"""LLM-callable contact tools.

Write:
  - :class:`AddContactTool`       — create a new contact.
  - :class:`AddContactNoteTool`   — add a note about a contact.
  - :class:`UpdateContactNoteTool` — update a specific note by id.
  - :class:`DeleteContactNoteTool` — remove a specific note.
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

from magi.agent.memory.contacts.store import ContactStore
from magi.agent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    caller_role_denied_reason,
)


logger = logging.getLogger("magi.agent.memory.contacts.tools")

# Tool author gate. After the 2024 role/admin split,
# ``role='admin'`` is no longer a reachable value — the
# ``admin`` boolean carries WebUI access rights instead.
# An operator contact typically has ``role='contact'`` and
# ``admin=True``; an assigned user may be their own
# operator (``role='assigned', admin=True``). The gate
# accepts either flavour; the registry's role filter
# can't see the admin bool so we widen the role-only
# filter to ``{'assigned'}`` and re-check the admin bool
# in ``_gate``. ``contact`` + ``admin=True`` passes; pure
# ``contact`` (no admin) is rejected here even though
# the registry allows it (defense in depth).
_WRITE_ROLES = frozenset({"assigned"})


def _gate(ctx: ToolContext) -> str | None:
    """Author check: ``admin=True`` OR ``role='assigned'``.

    Mirrors :func:`magi.channels.webui.api.tasks._enforce_creator_can_create`
    so the LLM-side tool and the API-side task gate agree
    on who can drive write operations. ``role='contact'``
    alone (no admin) is rejected; ``role='assigned'`` alone
    is accepted (an assigned user can manage their own
    contact directory); ``admin=True`` overrides role.
    """
    if ctx.admin:
        return None
    if ctx.caller_role == "assigned":
        return None
    return caller_role_denied_reason(ctx, _WRITE_ROLES)


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
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
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
            "role": {"type": "string", "description": "assigned/contact/guest. Default 'guest'."},
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)
        name = kwargs.get("name")
        if not isinstance(name, str) or not name.strip():
            return _err("name is required (non-empty string)")
        try:
            store = ContactStore(ctx.state_dir)
            view = store.create_contact(
                name=name,
                display_name=kwargs.get("display_name"),
                role=kwargs.get("role") or "guest",
                telegram_id=kwargs.get("telegram_id"),
                notes=kwargs.get("notes") or "",
            )
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- AddContactNoteTool -------------------------------------------------------


class AddContactNoteTool(Tool):
    """Add a note about an existing contact."""

    name = "add_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Add a note (one fact) about an existing contact. "
        "Each call creates a new note row — use for "
        "individual facts like 'Lily 在财务部' / "
        "'Mark prefer Slack'. Returns the note id for "
        "later update/delete. Contact must already exist "
        "(use add_contact first if needed)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {"type": "integer", "description": "contacts.id."},
            "note": {"type": "string", "description": "The note text. <=8 KB."},
        },
        "required": ["contact_id", "note"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)
        contact_id = kwargs.get("contact_id")
        if not isinstance(contact_id, int):
            return _err(f"contact_id must be int, got {type(contact_id).__name__}")
        try:
            store = ContactStore(ctx.state_dir)
            view = store.add_note(contact_id, note=kwargs["note"])
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- UpdateContactNoteTool ----------------------------------------------------


class UpdateContactNoteTool(Tool):
    """Update a specific note by id."""

    name = "update_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Update a specific contact note by note_id. "
        "Use when the operator says 'Lily 现在不负责这个了' — "
        "find the note via search_contacts first, note the id, "
        "then update it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "contact_notes.id."},
            "note": {"type": "string", "description": "Updated note text."},
        },
        "required": ["note_id", "note"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)
        note_id = kwargs.get("note_id")
        if not isinstance(note_id, int):
            return _err(f"note_id must be int, got {type(note_id).__name__}")
        try:
            store = ContactStore(ctx.state_dir)
            view = store.update_note(note_id, note=kwargs["note"])
        except (ValueError, LookupError) as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- DeleteContactNoteTool ----------------------------------------------------


class DeleteContactNoteTool(Tool):
    """Delete a specific note by id. Idempotent."""

    name = "delete_contact_note"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Delete a specific contact note by note_id. "
        "Idempotent — deleting a non-existent id returns success. "
        "Only use for individual notes, not the contact itself."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "contact_notes.id."},
        },
        "required": ["note_id"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)
        note_id = kwargs.get("note_id")
        if not isinstance(note_id, int):
            return _err(f"note_id must be int, got {type(note_id).__name__}")
        store = ContactStore(ctx.state_dir)
        existed = store.delete_note(note_id)
        return _ok({"note_id": note_id, "existed": existed})


# -- SearchContactsTool -------------------------------------------------------


class SearchContactsTool(Tool):
    """Search contacts by name or note content."""

    name = "search_contacts"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Search contacts by name or note content "
        "(case-insensitive substring). Returns contacts "
        "whose name or any note matches. Use when the "
        "operator says '记得 Mark 在哪吗' / "
        "'谁在负责 Q3 报销'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Substring to search for."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
        "required": ["query"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return _err("query is required")
        limit = int(kwargs.get("limit") or 20)
        store = ContactStore(ctx.state_dir)
        results = store.search(query, limit=limit)
        return _ok({
            "query": query,
            "matches": [v.to_dict() for v in results],
        })


__all__ = [
    "AddContactTool",
    "AddContactNoteTool",
    "UpdateContactNoteTool",
    "DeleteContactNoteTool",
    "SearchContactsTool",
]
