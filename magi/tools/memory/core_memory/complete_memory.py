"""``complete_memory`` tool — mark an ``ongoing`` row as
done.

Sets ``completed_at`` to the current UTC. The row stays
in the table for the audit trail but drops out of the
system-prompt formatter.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.complete_memory")


class CompleteMemoryTool(Tool):
    """Mark an ``ongoing`` row as done."""

    name = "complete_memory"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Mark an ongoing memory row as done. The row stays in the "
        "table for the audit trail but is no longer rendered in the "
        "system-prompt block. Use when the operator says "
        "'完成了' / '搞定了' / 'the project shipped'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the ongoing row to mark done.",
            },
        },
        "required": ["memory_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult(
                content=(
                    f"memory_id must be int, "
                    f"got {type(memory_id).__name__}"
                ),
                is_error=True,
            )
        try:
            bus = ctx.bus
            view = bus.memory_book.complete(memory_id)
        except LookupError as e:
            return ToolResult(content=str(e), is_error=True)
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)