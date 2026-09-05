"""``search_conversation_messages`` — substring search inside one conversation.

Jobs:
  SearchConversationMessagesJob — match ``query`` in that conversation,
  including archived messages.
"""

from __future__ import annotations

from typing import Any

from bus import SearchConversationMessagesJob
from tools.BaseTool import BaseTool, ToolResult

_PUBLISHER = "tools"
_LIMIT = 20


class SearchConversationMessagesTool(BaseTool):
    """Search messages in one conversation."""

    name = "search_conversation_messages"
    description = (
        "Search messages in one conversation by substring, including "
        "archived history. Use the session conversation_id. Prefer this "
        "when the user is asking about something said in this chat."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "integer",
                "description": "Conversation to search.",
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
        "required": ["conversation_id", "query"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            conversation_id = int(kwargs.get("conversation_id"))
        except (TypeError, ValueError):
            return ToolResult.err("conversation_id is required")
        query = kwargs.get("query")
        if conversation_id <= 0:
            return ToolResult.err("conversation_id is required")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.err("query is required (non-empty string)")
        limit = int(kwargs.get("limit") or _LIMIT)
        found = await self.publish(
            SearchConversationMessagesJob(
                publisher=_PUBLISHER,
                conversation_id=conversation_id,
                q=query.strip(),
                limit=limit,
            )
        )
        if found is None or found.messages is None:
            return ToolResult.err("message book is not available")
        return ToolResult.ok(
            {
                "query": query,
                "conversation_id": conversation_id,
                "messages": [message.to_dict() for message in found.messages],
            }
        )
