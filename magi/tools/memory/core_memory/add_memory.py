"""``add_memory`` tool — persist a new fact into MAGI's
mid-term memory.

The LLM calls this when the operator asks to remember
something ("记住 X" / "记下 Y" / "the contract is due on
9/30"). The body is markdown; the LLM is responsible
for the prose.

Person records are NOT writable here — they live in
:mod:`magi.tools.memory.contacts` and have their own
tool set (the LLM-managed directory of people the MAGI
knows about).

Admin gate: same as the API — only ``admin`` and
``assigned`` contacts can write to their own memory.
``contact`` and ``guest`` get ``is_error=True`` on every
write tool. Reads (no read tool yet — the system-prompt
block is the read path for v0) would carry the same
gate when added.

Bus plumbing: this tool talks to bus
(:class:`magi.bus.Bus`) via ``ctx.bus.memory_book``
— the Book owns the write invariants (kind membership
in :class:`~magi.bus.library.local.memoryBook.MemoryKind`,
subject non-empty + ≤200 chars, body non-empty + ≤8 KB,
priority 1..5) and
surfaces any violation as ``ValueError`` that we
translate to ``ToolResult.err`` here. The bus
service at bus Book API
is no longer imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.bus.library.local.memoryBook import (
    Memory,
    MemoryKind,
)
from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.memory.add_memory")


class AddMemoryTool(Tool):
    """Persist a new fact into MAGI's mid-term memory."""

    name = "add_memory"

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
        "Persist a new fact into MAGI's mid-term memory. "
        "Use when the operator says '记住 X' / '记下 Y' / "
        "'把 ... 记录下来' — or when the LLM judges a "
        "fact worth remembering across conversations "
        "(company policy, contract deadline, ongoing "
        "project). kinds: 'fact' (long-arc facts), "
        "'quick_note' (work in flight, has a completion). "
        "Person records are NOT written here — use the "
        "contacts tools for people."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(k.value for k in MemoryKind),
                "description": "fact | quick_note",
            },
            "subject": {
                "type": "string",
                "description": (
                    "Short title. <=200 chars. The bullet in the "
                    "system-prompt block renders this verbatim."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Full body. Markdown. <=8 KB. Repeating the "
                    "subject in the body is fine — the LLM often "
                    "re-structures the subject into the body "
                    "when it has more context."
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
        "required": ["kind", "subject", "body"],
    }

    @Tool.require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        assert ctx.bus is not None, "require_bus should have caught this"
        # Shape translation — kwargs → typed
        # :meth:`MemoryBook.add` arguments. The Book
        # owns the write invariants (subject non-empty
        # + ≤200 chars, body non-empty + ≤8 KB,
        # ``kind`` enum membership,
        # ``priority`` 1..5) so we don't re-check
        # them here. A violation raises ``ValueError``,
        # which the worker catches and surfaces as
        # ``is_error=True`` to the LLM.
        missing = [k for k in ("kind", "subject", "body") if not kwargs.get(k)]
        if missing:
            return ToolResult.err(f"add_memory requires fields: {', '.join(missing)}")
        if ctx.bus is None:
            return ToolResult.err("bus not available")
        try:
            record_id = ctx.bus.memory_book.add(Memory(
                contact_id=int(ctx.contact_id),
                kind=kwargs["kind"],
                subject=kwargs["subject"],
                body=kwargs["body"],
                priority=kwargs.get("priority", 3),
            ))
            view = ctx.bus.memory_book.get(memory_id=record_id)
            if view is None:
                raise RuntimeError(f"memory row {record_id} disappeared after insert")
        except ValueError as e:
            return ToolResult.err(f"add_memory failed: {e}")
        logger.info(
            "add_memory: row %s created for contact=%s kind=%r subject=%r",
            view.id,
            ctx.contact_id,
            view.kind,
            view.subject,
        )
        return ToolResult.ok({"created": view.to_dict()})
