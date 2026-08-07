"""``add_memory`` tool — persist a new fact into MAGI's
mid-term memory.

The LLM calls this when the operator asks to remember
something ("记住 X" / "记下 Y" / "the contract is due on
9/30"). The body is markdown; the LLM is responsible for
the prose.

Person records are NOT writable here — they live in
:mod:`magi.bus.jobs.services.contact` and have their own
tool set (the LLM-managed directory of people the MAGI
knows about).

Admin gate: same as the API — only ``admin`` and
``assigned`` contacts can write to their own memory.
``contact`` and ``guest`` get ``is_error=True`` on every
write tool. Reads (no read tool yet — the system-prompt
block is the read path for v0) would carry the same
gate when added.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.new_bus.library.local.memoryBook import ALL_KINDS, SOURCE_EVA
from magi.tools.base import Tool, ToolContext, ToolResult, require_bus

logger = logging.getLogger("magi.tools.memory.add_memory")


class AddMemoryTool(Tool):
    """Persist a new fact into MAGI's mid-term memory."""

    name = "add_memory"

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
        "Persist a new fact into MAGI's mid-term memory. "
        "Use when the operator says '记住 X' / '记下 Y' / "
        "'把 ... 记录下来' — or when the LLM judges a "
        "fact worth remembering across conversations "
        "(company policy, contract deadline, ongoing "
        "project). kinds: 'important' (long-arc facts), "
        "'ongoing' (work in flight, has a completion). "
        "Person records are NOT written here — use the "
        "contacts tools for people."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(ALL_KINDS),
                "description": "important | ongoing",
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
            "importance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": (
                    "1 (low) .. 5 (critical). 'important' rows "
                    "default to 4-5; 'ongoing' rows default to "
                    "2-3 so the operator can deprioritise."
                ),
            },
        },
        "required": ["kind", "subject", "body"],
    }

    @require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            bus = ctx.bus
            view = bus.memory_book.add(
                int(ctx.uid),
                kind=kwargs["kind"],
                subject=kwargs["subject"],
                body=kwargs["body"],
                importance=kwargs.get("importance", 3),
                source=SOURCE_EVA,
            )
        except (ValueError, KeyError) as e:
            return ToolResult(
                content=f"add_memory failed: {e}",
                is_error=True,
            )
        body = json.dumps(view.to_dict(), indent=2, ensure_ascii=False)
        if len(body) > 4 * 1024:
            body = body[: 4 * 1024] + "\n…(truncated)"
        return ToolResult(content=body, is_error=False)