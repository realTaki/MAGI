"""Schema-only actor effect for general MAGI-to-MAGI communication."""

from __future__ import annotations

from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult


class MessageMagiTool(Tool):
    """Ask another MAGI a question or send it a one-way message.

    The AgentWorker persists this effect with the transition by
    publishing a :class:`magi.bus.guild.sendA2AJob.SendA2AJob`
    onto ``bus.a2a_job_board`` (``a2a_invocations`` table). It must
    never be executed by ToolWorker, otherwise an A2A write could
    escape the actor's transactional outbox.
    """

    name = "message_magi"
    description = "Send a general message to another MAGI. Set expect_reply only when a later answer is required."
    input_schema = {
        "type": "object",
        "properties": {
            "magi_id": {"type": "integer", "description": "Target MAGI id (``magis_memberships.id``)."},
            "text": {"type": "string", "description": "Message to the peer MAGI."},
            "expect_reply": {"type": "boolean", "default": False},
        },
        "required": ["magi_id", "text"],
    }

    async def run(self, _ctx: ToolContext, **_kwargs: Any) -> ToolResult:
        return ToolResult(
            content="message_magi is scheduled by the actor and must not run in ToolWorker",
            is_error=True,
        )
