"""``search_contacts`` tool — search contacts by name or by
note content.

Returns the matching contacts and a sample of their
notes. Use when the operator says '查一下 Lily / 谁在财务部'.

Catalog filter: ``ALLOWED_ROLES = {"admin", "assigned"}``.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.contacts_book``
for the contact-side join (name + note match,
``last_seen_at`` ordering) and ``self.bus.contact_notes_book``
for the per-contact note sample. The legacy service at
bus Book API is no longer
imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.search_contacts")


class SearchContactsTool(BaseTool):
    """Search contacts by name or by note content."""

    name = "search_contacts"
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Search the contact directory by name or by note "
        "text. Returns the matching contacts and a sample "
        "of their notes. Use when the operator says "
        "'查一下 Lily / 谁在财务部'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search string (matches name or note text, case-insensitive substring)."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
                "description": ("Max contacts to return. Default 20."),
            },
            "notes_per_contact": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 5,
                "description": (
                    "Max notes to attach per contact in the "
                    "response. Default 5 (newest first). "
                    "Set to 0 to skip notes entirely."
                ),
            },
        },
        "required": ["query"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.err("query is required (non-empty string)")
        limit = int(kwargs.get("limit") or 20)
        notes_per_contact = int(kwargs.get("notes_per_contact") or 5)

        contacts = self.bus.contacts_book.search(
            query=query,
            limit=limit,
        )

        results: list[dict] = []
        for contact in contacts:
            entry: dict = contact.to_dict()
            if notes_per_contact > 0:
                # Slice in-place to bound the response —
                # ``list_for_contact`` returns the full
                # corpus sorted newest-first, so a prefix
                # is the same "sample" the legacy
                # ``ContactView`` returned.
                notes = self.bus.contact_notes_book.list_for_contact(
                    contact_id=contact.id,
                )[:notes_per_contact]
                entry["notes"] = [n.to_dict() for n in notes]
            results.append(entry)

        logger.info(
            "search_contacts: query=%r limit=%s returned=%s",
            query,
            limit,
            len(results),
        )
        return ToolResult.ok(
            {
                "query": query,
                "count": len(results),
                "contacts": results,
            }
        )
