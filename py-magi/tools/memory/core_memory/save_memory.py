"""``save_memory`` tool — create or patch a MemoryBook row through Jobs."""

from __future__ import annotations

import logging
from typing import Any

from bus import CreateMemoryJob, GetMemoryJob, MemoryKind, UpdateMemoryJob
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.save_memory")

_PUBLISHER = "tools"


class SaveMemoryTool(BaseTool):
    """Create or patch a runtime-local memory."""

    name = "save_memory"

    description = (
        "Create or update a memory. Omit memory_id to create; pass "
        "memory_id to patch. topic and detail are required when creating. "
        "kinds: temporary, short_term, long_term. Person records are not "
        "written here — use the contacts tools for people."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of an existing row to patch. Omit to create.",
            },
            "topic": {
                "type": "string",
                "description": "Short title. Required when creating.",
            },
            "detail": {
                "type": "string",
                "description": "Full body. Required when creating.",
            },
            "kind": {
                "type": "string",
                "enum": [kind.value for kind in MemoryKind],
                "description": "temporary | short_term | long_term. Defaults to temporary.",
            },
            "archived": {
                "type": "boolean",
                "description": "Set true to hide a row from the active memory list.",
            },
        },
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        if kwargs.get("memory_id") is not None:
            return await self._update(**kwargs)
        return await self._create(**kwargs)

    async def _create(self, **kwargs: Any) -> ToolResult:
        topic = kwargs.get("topic")
        detail = kwargs.get("detail")
        if not isinstance(topic, str) or not topic.strip():
            return ToolResult.err("save_memory create requires topic")
        if not isinstance(detail, str) or not detail.strip():
            return ToolResult.err("save_memory create requires detail")
        kind = MemoryKind.TEMPORARY
        if kwargs.get("kind") is not None:
            try:
                kind = MemoryKind(kwargs["kind"])
            except ValueError:
                return ToolResult.err(
                    "save_memory kind must be temporary, short_term, or long_term"
                )
        created = await self.publish(
            CreateMemoryJob(
                publisher=_PUBLISHER,
                topic=topic.strip(),
                detail=detail,
                kind=kind,
            )
        )
        if created is None or created.memory_id is None:
            return ToolResult.err("memory book is not available")
        fetched = await self.publish(
            GetMemoryJob(publisher=_PUBLISHER, memory_id=created.memory_id)
        )
        memory = None if fetched is None else fetched.memory
        logger.info("save_memory: row %s created kind=%r topic=%r", created.memory_id, kind, topic)
        return ToolResult.ok({"created": None if memory is None else memory.to_dict()})

    async def _update(self, **kwargs: Any) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        existing = await self.publish(GetMemoryJob(publisher=_PUBLISHER, memory_id=memory_id))
        if existing is None:
            return ToolResult.err("memory book is not available")
        if existing.memory is None:
            return ToolResult.err(f"memory {memory_id} not found")
        kind = None
        if kwargs.get("kind") is not None:
            try:
                kind = MemoryKind(kwargs["kind"])
            except ValueError:
                return ToolResult.err(
                    "save_memory kind must be temporary, short_term, or long_term"
                )
        topic = kwargs.get("topic")
        detail = kwargs.get("detail")
        archived = kwargs.get("archived")
        updated = await self.publish(
            UpdateMemoryJob(
                publisher=_PUBLISHER,
                memory_id=memory_id,
                topic=None if topic is None else str(topic),
                detail=None if detail is None else str(detail),
                kind=kind,
                archived=None if archived is None else bool(archived),
            )
        )
        if updated is None:
            return ToolResult.err("memory book is not available")
        fetched = await self.publish(GetMemoryJob(publisher=_PUBLISHER, memory_id=memory_id))
        memory = None if fetched is None else fetched.memory
        logger.info("save_memory: row %s updated", memory_id)
        return ToolResult.ok({"memory": None if memory is None else memory.to_dict()})
