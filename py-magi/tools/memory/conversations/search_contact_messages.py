"""``search_contact_messages`` — substring search of one contact across conversations.

Jobs:
  SearchContactMessagesJob — match ``query`` on that speaker's messages
  in every conversation, including archived history.
"""

from __future__ import annotations

from typing import Any

from bus import SearchContactMessagesJob
from tools.BaseTool import BaseTool, ToolResult

_PUBLISHER = "tools"
_LIMIT = 20


class SearchContactMessagesTool(BaseTool):
    """Search one contact's messages across conversations."""

    name = "search_contact_messages"
    description = (
        "Search one contact's messages across conversations by substring, "
        "including archived history. Pass the contact_id of the speaker. "
        "Use when recalling what that person said in other chats."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "integer",
                "description": "Speaker whose messages to search.",
            },
            "query": {
                "type": "string",
                "description": "Case-insensitive substring to find.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _LIMIT,
                "default": _LIMIT,
                "description": f"Max hits to return. Default {_LIMIT}.",
            },
        },
        "required": ["contact_id", "query"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            contact_id = int(kwargs.get("contact_id"))
        except (TypeError, ValueError):
            return ToolResult.err("contact_id is required")
        query = kwargs.get("query")
        if contact_id < 0:
            return ToolResult.err("contact_id is required")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.err("query is required (non-empty string)")
        limit = int(kwargs.get("limit") or _LIMIT)
        found = await self.publish(
            SearchContactMessagesJob(
                publisher=_PUBLISHER,
                contact_id=contact_id,
                q=query.strip(),
                limit=limit,
            )
        )
        if found is None or found.messages is None:
            return ToolResult.err("message book is not available")
        return ToolResult.ok(
            {
                "query": query,
                "contact_id": contact_id,
                "messages": [message.to_dict() for message in found.messages],
            }
        )
