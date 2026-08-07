"""``search_contacts`` tool — search contacts by name or by
note content.

Returns the matching contacts and a sample of their
notes. Use when the operator says '查一下 Lily / 谁在财务部'.

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

logger = logging.getLogger("magi.tools.memory.search_contacts")


class SearchContactsTool(Tool):
    """Search contacts by name or by note content."""

    name = "search_contacts"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
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
            return ToolResult(
                content="query is required (non-empty string)",
                is_error=True,
            )
        bus = ctx.bus
        views = bus.contacts_book.search(query, limit=limit)
        body = json.dumps(
            {
                "query": query,
                "count": len(views),
                "contacts": [v.to_dict() for v in views],
            },
            indent=2,
            ensure_ascii=False,
        )
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)