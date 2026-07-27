"""LLM-callable contact tools.

Two write tools, one read:

  - :class:`AddContactTool` — create a new contact row
    (name, optional display_name, telegram_id, notes).
  - :class:`AddContactNoteTool` — record notes about an
    existing contact.
  - :class:`UpdateContactTool` — patch ``notes`` / ``role``.
  - :class:`SearchContactsTool` — search contact directory.

Contacts are never deleted via LLM tools.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from magi.agent.memory.contacts.store import ContactStore
from magi.agent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    caller_role_denied_reason,
)


logger = logging.getLogger("magi.agent.memory.contacts.tools")

_WRITE_ROLES = frozenset({"admin", "assigned"})


def _gate(ctx: ToolContext) -> str | None:
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
    """Create a new contact in the directory."""

    name = "add_contact"

    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Create a new contact (person) in the directory. "
        "Use when the operator says '添加 Lily 到团队' / "
        "'把 Mark 加进来'. Name is required. "
        "display_name, telegram_id, and notes are optional. "
        "To add notes about an existing contact, use "
        "add_contact_note instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Contact name (required, unique).",
            },
            "display_name": {
                "type": "string",
                "description": "Display name (optional).",
            },
            "telegram_id": {
                "type": "integer",
                "description": "Telegram user id (optional).",
            },
            "notes": {
                "type": "string",
                "description": "Initial notes (optional).",
            },
            "role": {
                "type": "string",
                "description": "Role: admin, assigned, contact, or guest. Default 'contact'.",
            },
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
                role=kwargs.get("role") or "contact",
                telegram_id=kwargs.get("telegram_id"),
                notes=kwargs.get("notes") or "",
            )
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- AddContactNoteTool -------------------------------------------------------


class AddContactNoteTool(Tool):
    """Record notes about an existing contact."""

    name = "add_contact_note"

    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Record what the MAGI knows about an existing contact. "
        "Use when the operator says '记住 Lily 在财务部' / "
        "'Mark 是我们 CTO' / '记得 Bob prefer Slack over email'. "
        "The contact must already exist (use add_contact first "
        "if needed). Idempotent — re-calling patches notes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "integer",
                "description": "contacts.id of the person.",
            },
            "notes": {
                "type": "string",
                "description": "Free-form markdown notes. <=8 KB.",
            },
        },
        "required": ["contact_id", "notes"],
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
            view = store.add_note(contact_id, notes=kwargs["notes"])
        except ValueError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- UpdateContactTool --------------------------------------------------------


class UpdateContactTool(Tool):
    """Patch an existing contact's notes or role."""

    name = "update_contact"

    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Patch an existing contact's notes or role by id. "
        "Use when the operator says 'Lily 现在不负责这块了' / "
        "'Mark 的 role 改了'. Contact must already exist."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {"type": "integer", "description": "id of the contact row."},
            "notes": {"type": "string"},
            "role": {"type": "string"},
        },
        "required": ["contact_id"],
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
            view = store.update_notes(
                contact_id,
                notes=kwargs.get("notes"),
                role=kwargs.get("role"),
            )
        except LookupError as e:
            return _err(str(e))
        return _ok(view.to_dict())


# -- SearchContactsTool -------------------------------------------------------


class SearchContactsTool(Tool):
    """Search the contact directory."""

    name = "search_contacts"

    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Search the contact directory by name or notes "
        "(case-insensitive substring). Use when the operator "
        "says '记得 Mark 在哪吗' / '谁在负责 Q3 报销'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring to search for.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
            },
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
    "UpdateContactTool",
    "SearchContactsTool",
]
