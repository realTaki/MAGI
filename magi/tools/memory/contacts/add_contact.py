"""``add_contact`` tool — create a new contact in the
directory.

Notes about an existing contact are recorded separately
via :mod:`magi.tools.memory.add_contact_note`; this tool
only takes an initial ``notes`` field as a convenience for
"create + first observation" flows.

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

logger = logging.getLogger("magi.tools.memory.add_contact")


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
            "role": {"type": "string", "description": "assigned/guest. Default 'guest'."},
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        name = kwargs.get("name")
        if not isinstance(name, str) or not name.strip():
            return ToolResult(
                content="name is required (non-empty string)",
                is_error=True,
            )
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
            return ToolResult(content=str(e), is_error=True)
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)