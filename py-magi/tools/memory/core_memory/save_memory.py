"""``save_memory`` tool — create or patch a mid-term memory row.

Omit ``memory_id`` to create; pass ``memory_id`` to patch.
``kind`` and ``contact_id`` are immutable on update — delete and
re-add if those need to change.

Person records are not writable here; they live in
:mod:`tools.memory.contacts`.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from old_bus.firmwares.books.local.memoryBook import (
    Memory,
    MemoryKind,
)
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.save_memory")


class SaveMemoryTool(BaseTool):
    """Create or patch a mid-term memory row."""

    name = "save_memory"

    description = (
        "Create or update a mid-term memory row. "
        "Use when the operator says '记住 X' / '记下 Y' / "
        "'更新 X' / '改成 ...' / 'the deadline is now 10/15'. "
        "Omit memory_id to create: kind, subject, and body are "
        "required. Pass memory_id to patch: subject, body, and "
        "priority are optional; kind and contact_id are immutable "
        "(delete + re-add to change those). "
        "kinds: 'fact' (long-arc facts), 'quick_note' (work in "
        "flight, has a completion). Person records are NOT "
        "written here — use the contacts tools for people."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": (
                    "id of an existing row to patch. Omit to create. "
                    "Visible in the create result and in the "
                    "system-prompt block as 'memory id N: ...'."
                ),
            },
            "kind": {
                "type": "string",
                "enum": sorted(k.value for k in MemoryKind),
                "description": "fact | quick_note. Required when creating; ignored on update.",
            },
            "subject": {
                "type": "string",
                "description": (
                    "Short title. <=200 chars. Required when creating. "
                    "The bullet in the system-prompt block renders this verbatim."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Full body. Markdown. <=8 KB. Required when creating."
                ),
            },
            "priority": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": (
                    "1 (low) .. 5 (critical). 'fact' rows "
                    "default to 4-5; 'quick_note' rows default to "
                    "2-3 so the operator can deprioritise."
                ),
            },
        },
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        if kwargs.get("memory_id") is not None:
            return await self._update(**kwargs)
        return await self._create(**kwargs)

    async def _create(self, **kwargs: Any) -> ToolResult:
        missing = [
            key
            for key in ("kind", "subject", "body")
            if not isinstance(kwargs.get(key), str) or not kwargs[key].strip()
        ]
        if missing:
            return ToolResult.err(f"save_memory create requires fields: {', '.join(missing)}")
        try:
            kind = MemoryKind(kwargs["kind"])
        except ValueError:
            return ToolResult.err("save_memory kind must be 'fact' or 'quick_note'")
        record_id = self.bus.memory_book.add(Memory(
            contact_id=int(kwargs.get("contact_id") or 0),
            kind=kind,
            subject=kwargs["subject"],
            body=kwargs["body"],
            priority=kwargs.get("priority", 3),
        ))
        view = self.bus.memory_book.get(record_id)
        logger.info(
            "save_memory: row %s created for contact=%s kind=%r subject=%r",
            view.id,
            int(kwargs.get("contact_id") or 0),
            view.kind,
            view.subject,
        )
        return ToolResult.ok({"created": view.to_dict()})

    async def _update(self, **kwargs: Any) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        ct_id = int(kwargs.get("contact_id") or 0)
        existing = self.bus.memory_book.get(memory_id)
        if existing is None or existing.contact_id != ct_id:
            return ToolResult.err(
                f"memory {memory_id} not found or not owned by the calling operator"
            )
        candidate = replace(
            existing,
            subject=kwargs.get("subject", existing.subject),
            body=kwargs.get("body", existing.body),
            priority=kwargs.get("priority", existing.priority),
        )
        if not self.bus.memory_book.update(candidate):
            return ToolResult.err(f"memory {memory_id} no longer exists")
        view = self.bus.memory_book.get(memory_id)
        logger.info(
            "save_memory: row %s updated by %s",
            memory_id,
            ct_id,
        )
        return ToolResult.ok({"memory": view.to_dict()})
