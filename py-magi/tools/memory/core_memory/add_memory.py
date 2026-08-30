"""``add_memory`` tool — persist a new fact into MAGI's
mid-term memory.

The LLM calls this when the operator asks to remember
something ("记住 X" / "记下 Y" / "the contract is due on
9/30"). The body is markdown; the LLM is responsible
for the prose.

Person records are NOT writable here — they live in
:mod:`tools.memory.contacts` and have their own
tool set (the LLM-managed directory of people the MAGI
knows about).

Admin gate: same as the API — only ``admin`` and
``assigned`` contacts can write to their own memory.
``contact`` and ``guest`` get ``is_error=True`` on every
write tool. Reads (no read tool yet — the system-prompt
block is the read path for v0) would carry the same
gate when added.

Bus plumbing: this tool talks to bus (:class:`bus.Bus`) via
``self.bus.memory_book``.  It validates its command vocabulary at ingress;
the Book persists free text without imposing a second input policy.
"""

from __future__ import annotations

import logging
from typing import Any

from old_bus.firmwares.books.local.memoryBook import (
    Memory,
    MemoryKind,
)
from tools.base import Tool, ToolResult

logger = logging.getLogger("tools.memory.add_memory")


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
        **kwargs: Any,
    ) -> ToolResult:
        # Tool parameters are an ingress boundary. The Book deliberately
        # persists unconstrained free text.
        missing = [
            key
            for key in ("kind", "subject", "body")
            if not isinstance(kwargs.get(key), str) or not kwargs[key].strip()
        ]
        if missing:
            return ToolResult.err(f"add_memory requires fields: {', '.join(missing)}")
        try:
            kind = MemoryKind(kwargs["kind"])
        except ValueError:
            return ToolResult.err("add_memory kind must be 'fact' or 'quick_note'")
        if self.bus is None:
            return ToolResult.err("bus not available")
        try:
            record_id = self.bus.memory_book.add(Memory(
                contact_id=int(kwargs.get("contact_id") or 0),
                kind=kind,
                subject=kwargs["subject"],
                body=kwargs["body"],
                priority=kwargs.get("priority", 3),
            ))
            view = self.bus.memory_book.get(record_id)
            if view is None:
                raise RuntimeError(f"memory row {record_id} disappeared after insert")
        except ValueError as e:
            return ToolResult.err(f"add_memory failed: {e}")
        logger.info(
            "add_memory: row %s created for contact=%s kind=%r subject=%r",
            view.id,
            int(kwargs.get("contact_id") or 0),
            view.kind,
            view.subject,
        )
        return ToolResult.ok({"created": view.to_dict()})
