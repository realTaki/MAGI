"""``update_memory`` tool — patch an existing memory row
by id.

The LLM finds the id via the system-prompt block
("memory id 17 says …"). Mutable fields only — ``kind``
and ``uid`` are intentionally not editable to keep the
row's identity stable across edits.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.update_memory")


class UpdateMemoryTool(Tool):
    """Patch an existing memory row by id."""

    name = "update_memory"

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
        "Patch an existing memory row by id. Use when the operator "
        "says '更新 X' / '改成 ...' / 'the deadline is now 10/15'. "
        "Mutable: subject, body, importance. Immutable: kind, "
        "uid (delete + re-add if you really need to change "
        "those)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the row to patch (from add_memory result, or visible in the system-prompt block as 'memory id N: ...').",
            },
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5},
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
        try:
            bus = ctx.bus
            view = bus.memory_book.update(
                memory_id,
                subject=kwargs.get("subject"),
                body=kwargs.get("body"),
                importance=kwargs.get("importance"),
            )
        except LookupError as e:
            return ToolResult(content=str(e), is_error=True)
        except ValueError as e:
            return ToolResult(
                content=f"update_memory failed: {e}",
                is_error=True,
            )
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)