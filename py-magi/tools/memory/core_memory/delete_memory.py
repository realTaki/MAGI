"""``delete_memory`` — remove a MemoryBook row.

Jobs:
  GetMemoryJob — tell the caller whether the id existed.
  DeleteMemoryJob — delete the row; missing id is still success.
"""

from __future__ import annotations

import logging
from typing import Any

from bus import DeleteMemoryJob, GetMemoryJob
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.delete_memory")

_PUBLISHER = "tools"


class DeleteMemoryTool(BaseTool):
    """Delete a memory row."""

    name = "delete_memory"

    description = (
        "Delete a memory row by id. Idempotent — deleting a missing id "
        "returns success without leaking extra detail."
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
    async def run(self, **kwargs: Any) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        existing = await self.publish(GetMemoryJob(publisher=_PUBLISHER, memory_id=memory_id))
        if existing is None:
            return ToolResult.err("memory book is not available")
        existed = existing.memory is not None
        deleted = await self.publish(DeleteMemoryJob(publisher=_PUBLISHER, memory_id=memory_id))
        if deleted is None:
            return ToolResult.err("memory book is not available")
        logger.info("delete_memory: row %s existed=%s", memory_id, existed)
        return ToolResult.ok({"memory_id": memory_id, "existed": existed})
