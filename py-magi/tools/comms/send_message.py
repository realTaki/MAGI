"""``send_message`` — deliver text to an existing conversation.

Jobs:
  GetConversationJob — confirm the conversation exists before sending.
  DeliveryNotify — enqueue the outbound body for the channel Worker.
"""

from __future__ import annotations

import asyncio
from typing import Any

from bus import DeliveryNotify, GetConversationJob
from tools.BaseTool import BaseTool, ToolResult


class SendMessageTool(BaseTool):
    """Deliver text to a conversation through ``DeliveryNotify``."""

    name = "send_message"
    description = (
        "Send a message to a conversation. Use when the operator "
        "should see text in that conversation without waiting for "
        "the final assistant reply."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "integer",
                "description": "Conversation to deliver to.",
            },
            "text": {
                "type": "string",
                "description": "Message body.",
            },
        },
        "required": ["conversation_id", "text"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            conversation_id = int(kwargs.get("conversation_id"))
        except (TypeError, ValueError):
            return ToolResult.err("conversation_id is required")
        text = kwargs.get("text")
        if conversation_id <= 0:
            return ToolResult.err("conversation_id is required")
        if not isinstance(text, str) or not text:
            return ToolResult.err("text is required")

        conversations = self.bus.board(GetConversationJob)
        deliveries = self.bus.board(DeliveryNotify)
        if conversations is None or deliveries is None:
            return ToolResult.err("delivery is not available")
        found = await asyncio.to_thread(
            conversations.publish,
            GetConversationJob(publisher="tools", conversation_id=conversation_id),
        )
        if found.conversation is None:
            return ToolResult.err(f"unknown conversation {conversation_id}")
        await asyncio.to_thread(
            deliveries.publish,
            DeliveryNotify(publisher="tools", conversation_id=conversation_id, text=text),
        )
        return ToolResult(content=f"queued to conversation {conversation_id}")


__all__ = ["SendMessageTool"]
