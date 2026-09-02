"""``complete_memory`` — hide a memory from the active list.

Jobs:
  GetMemoryJob — fail if the id does not exist.
  UpdateMemoryJob — set ``archived=True`` (row stays in the book).
"""

from __future__ import annotations

import logging
from typing import Any

from bus import GetMemoryJob, UpdateMemoryJob
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.complete_memory")

_PUBLISHER = "tools"


class CompleteMemoryTool(BaseTool):
    """Hide one memory from the active list by archiving it."""

    name = "complete_memory"

    description = (
        "Archive a memory row so it drops out of the active memory list. "
        "The row stays in the book. Use when the operator says the item "
        "is done or no longer current."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the row to archive.",
            },
        },
        "required": ["memory_id"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        existing = await self.publish(GetMemoryJob(publisher=_PUBLISHER, memory_id=memory_id))
        if existing is None:
            return ToolResult.err("memory book is not available")
        if existing.memory is None:
            return ToolResult.err(f"memory {memory_id} not found")
        updated = await self.publish(
            UpdateMemoryJob(publisher=_PUBLISHER, memory_id=memory_id, archived=True)
        )
        if updated is None:
            return ToolResult.err("memory book is not available")
        fetched = await self.publish(GetMemoryJob(publisher=_PUBLISHER, memory_id=memory_id))
        memory = None if fetched is None else fetched.memory
        logger.info("complete_memory: row %s archived", memory_id)
        return ToolResult.ok({"memory": None if memory is None else memory.to_dict()})
