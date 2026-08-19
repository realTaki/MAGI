"""``update_memory`` tool — patch an existing memory row
by id.

The LLM finds the id via the system-prompt block
("memory id 17 says …"). Mutable fields only — ``kind``
and ``contact_id`` are intentionally not editable to keep the
row's identity stable across edits.

Strict per-contact privacy: a tool call from operator A
never sees operator B's rows, even if the LLM asks for
an id it doesn't own — the row is missing rather than
shared.

Bus plumbing: this tool talks to bus
(:class:`magi.bus.Bus`) via ``ctx.bus.memory_book``
— the Book owns the write invariants for ``subject``,
``body`` and ``priority`` (non-empty + length caps,
``priority`` 1..5) and surfaces any violation as
:class:`ValueError` that we translate to
``ToolResult.err`` here. Authorization ("does the caller
own this row?") lives at the tool layer.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.update_memory")


class UpdateMemoryTool(Tool):
    """Patch an existing memory row owned by the calling operator."""

    name = "update_memory"

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
        "Patch an existing memory row by id. Use when the operator "
        "says '更新 X' / '改成 ...' / 'the deadline is now 10/15'. "
        "Mutable: subject, body, priority. Immutable: kind, "
        "contact_id (delete + re-add if you really need to change "
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
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["memory_id"],
    }

    @Tool.require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        assert ctx.bus is not None, "require_bus should have caught this"
        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return ToolResult.err(f"memory_id must be int, got {type(memory_id).__name__}")
        ct_id = int(ctx.contact_id)
        # Strict per-contact privacy — auth lives at the
        # tool layer, not in the Book. ``MemoryBook.update``
        # is a pure data write; we ``get`` + check
        # ``row.contact_id == caller`` before any write fires.
        # Same TOCTOU comment as the action-item /
        # complete-memory tools.
        existing = ctx.bus.memory_book.get(memory_id)
        if existing is None or existing.contact_id != ct_id:
            return ToolResult.err(
                f"memory {memory_id} not found or not owned by the calling operator"
            )
        try:
            candidate = replace(
                existing,
                subject=kwargs.get("subject", existing.subject),
                body=kwargs.get("body", existing.body),
                priority=kwargs.get("priority", existing.priority),
            )
            if not ctx.bus.memory_book.update(candidate):
                return ToolResult.err(f"memory {memory_id} no longer exists")
            view = ctx.bus.memory_book.get(memory_id)
            assert view is not None
        except LookupError as e:
            return ToolResult.err(str(e))
        except ValueError as e:
            return ToolResult.err(f"update_memory failed: {e}")
        logger.info(
            "update_memory: row %s updated by %s",
            memory_id,
            ct_id,
        )
        return ToolResult.ok({"memory": view.to_dict()})
