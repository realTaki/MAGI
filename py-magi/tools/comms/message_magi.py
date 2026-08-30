"""Schema-only actor effect for durable MAGIS-internal A2A communication."""

from __future__ import annotations

from typing import Any

from tools.base import Tool, ToolResult


class MessageMagiTool(Tool):
    """Ask another MAGI a question or send it a one-way message.

    The AgentWorker persists this effect to a shared MAGIS request or notify
    board.  It must never be executed by ToolWorker: it is an actor effect,
    not a local executable tool.
    """

    name = "message_magi"
    description = (
        "Send a durable message to another MAGI in this MAGIS. "
        "Use notify for one-way information and request for one required answer."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "magi_id": {
                "type": "integer",
                "description": "Target MAGI id from the MAGIS collaboration directory.",
            },
            "text": {"type": "string", "description": "Message to the peer MAGI."},
            "mode": {
                "type": "string",
                "enum": ["notify", "request"],
                "description": "notify never expects a reply; request receives one response.",
            },
            "deadline_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3600,
                "default": 120,
            },
        },
        "required": ["magi_id", "text", "mode"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        _ = kwargs
        return ToolResult(
            content="message_magi is scheduled by the actor and must not run in ToolWorker",
            is_error=True,
        )
