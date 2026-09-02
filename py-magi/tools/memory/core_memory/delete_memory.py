"""``delete_memory`` tool — remove a memory row.

Strict per-contact privacy: a tool call from operator A
never sees operator B's rows, even if the LLM asks for
an id it doesn't own — the row is missing rather than
shared.

Bus plumbing: this tool talks to bus
(:class:`bus.Bus`) via ``self.bus.memory_book``
— the Book is a pure data delete and returns whether
the row existed. Authorization ("does the caller own
this row?") lives here at the tool layer so we don't
leak existence across contacts.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.delete_memory")


class DeleteMemoryTool(BaseTool):
    """Remove a memory row owned by the calling operator."""

    name = "delete_memory"

    description = (
        "Delete a memory row by id. Idempotent — deleting a "
        "non-existent or non-owned id returns success without "
        "leaking existence. Use when the operator says '忘了 X' / "
        "'那条记错了删掉'."
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

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        ct_id = int(kwargs.get("contact_id") or 0)
        # Strict per-contact privacy — auth lives at the
        # tool layer, not in the Book. ``MemoryBook.delete``
        # is a pure data delete; we ``get`` + check
        # ``row.contact_id == caller`` before any delete fires so
        # cross-contact delete attempts return a generic
        # ``not found / not owned`` without revealing
        # existence. Same TOCTOU comment as the action-item
        # / complete-memory tools.
        existing = self.bus.memory_book.get(memory_id)
        if existing is None or existing.contact_id != ct_id:
            return ToolResult.err(
                f"memory {memory_id} not found or not owned by the calling operator"
            )
        existed = self.bus.memory_book.delete(memory_id)
        logger.info(
            "delete_memory: row %s deleted by %s (existed=%s)",
            memory_id,
            ct_id,
            existed,
        )
        return ToolResult.ok({"memory_id": memory_id, "existed": existed})
