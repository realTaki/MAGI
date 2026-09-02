"""``search_contacts`` tool — filter Contact + note Jobs in-process."""

from __future__ import annotations

import logging
from typing import Any

from bus import ListContactNotesJob, ListContactsJob
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.search_contacts")

_PUBLISHER = "tools"


class SearchContactsTool(BaseTool):
    """Search contacts by name, nickname, or note text."""

    name = "search_contacts"
    description = (
        "Search the contact directory by name, nickname, or note text. "
        "Returns matching contacts and a sample of their notes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Case-insensitive substring over name, nickname, or notes.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
                "description": "Max contacts to return. Default 20.",
            },
            "notes_per_contact": {
                "type": "integer",
                "minimum": 0,
                "maximum": 50,
                "default": 5,
                "description": "Max notes to attach per contact. Default 5.",
            },
        },
        "required": ["query"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.err("query is required (non-empty string)")
        needle = query.strip().lower()
        limit = int(kwargs.get("limit") or 20)
        notes_per_contact = int(kwargs.get("notes_per_contact") or 5)
        listed = await self.publish(ListContactsJob(publisher=_PUBLISHER))
        if listed is None or listed.contacts is None:
            return ToolResult.err("contact book is not available")

        matches: list[dict[str, Any]] = []
        for contact in listed.contacts:
            notes_result = await self.publish(
                ListContactNotesJob(publisher=_PUBLISHER, contact_id=contact.id)
            )
            notes = [] if notes_result is None or notes_result.contact_notes is None else notes_result.contact_notes
            haystacks = [
                contact.name or "",
                contact.nickname or "",
                *(item.note or "" for item in notes),
            ]
            if not any(needle in text.lower() for text in haystacks):
                continue
            entry = contact.to_dict()
            if notes_per_contact > 0:
                entry["notes"] = [item.to_dict() for item in notes[:notes_per_contact]]
            matches.append(entry)
            if len(matches) >= limit:
                break

        logger.info("search_contacts: query=%r returned=%s", query, len(matches))
        return ToolResult.ok({"query": query, "contacts": matches})
