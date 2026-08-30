"""``complete_memory`` tool — mark an ``ongoing`` row as
done.

Sets ``completed_at`` to the current UTC. The row stays
in the table for the audit trail but drops out of the
system-prompt formatter.

Strict per-contact privacy: a tool call from operator A
never sees operator B's rows, even if the LLM asks for
an id it doesn't own — the row is missing rather than
shared.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.memory_book``
— the Book is a pure data write and surfaces a
:class:`LookupError` for missing rows. Authorization
("does the caller own this row?") lives here at the
tool layer so we don't need to duplicate it in every
caller of the Book.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.base import Tool, ToolResult

logger = logging.getLogger("tools.memory.complete_memory")


class CompleteMemoryTool(Tool):
    """Mark a ``quick_note`` row as done."""

    name = "complete_memory"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The agent worker resolves the operator's role from the
    # Contact row and filters the tool menu so non-eligible
    # callers never see these tools in the LLM's menu.
    # ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Mark a quick_note memory row as done. The row stays in the "
        "table for the audit trail but is no longer rendered in the "
        "system-prompt block. Use when the operator says "
        "'完成了' / '搞定了' / 'the project shipped'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the quick_note row to mark done.",
            },
        },
        "required": ["memory_id"],
    }

    @Tool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        ct_id = int(kwargs.get("contact_id") or 0)
        # Strict per-contact privacy — auth lives at the
        # tool layer, not in the Book. ``MemoryBook.complete``
        # is a pure data write; we ``get`` + check
        # ``row.contact_id == caller`` before any write fires.
        # The TOCTOU window is acceptable for the
        # single-writer chat tool (same comment as
        # ``complete_action_item``).
        existing = self.bus.memory_book.get(memory_id)
        if existing is None or existing.contact_id != ct_id:
            return ToolResult.err(
                f"memory {memory_id} not found or not owned by the calling operator"
            )
        try:
            view = self.bus.memory_book.complete(memory_id=memory_id)
        except LookupError as e:
            return ToolResult.err(str(e))
        logger.info(
            "complete_memory: row %s completed by %s",
            memory_id,
            ct_id,
        )
        return ToolResult.ok({"memory": view.to_dict()})
