"""``delete_memory`` tool — remove a memory row.

Idempotent — deleting a non-existent id is a successful
no-op. The LLM can retry without seeing a false
``is_error``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.delete_memory")


class DeleteMemoryTool(Tool):
    """Remove a memory row."""

    name = "delete_memory"

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
        "Delete a memory row by id. Idempotent — deleting a "
        "non-existent id returns success. Use when the operator "
        "says '忘了 X' / '那条记错了删掉'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the row to remove.",
            },
        },
        "required": ["memory_id"],
    }

    @Tool.require_bus
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
        existed = ctx.bus.memory_book.delete(memory_id)
        body = json.dumps(
            {"memory_id": memory_id, "existed": existed},
            indent=2,
            ensure_ascii=False,
        )
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)