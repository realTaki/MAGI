"""``load_skill`` tool — fetch one skill's markdown body on demand.

The system prompt lists each skill's name and description. Call this
when that summary is not enough and the full runbook is needed.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from bus import GetSkillJob, JobStatus
from tools.BaseTool import BaseTool, ToolResult

_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


class LoadSkillTool(BaseTool):
    """Read the full body of a registered skill."""

    name = "load_skill"

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    description = (
        "Read the full body of a registered skill. Use when "
        "the system prompt's 'Available skills' summary is not "
        "enough — for example when you need step-by-step "
        "instructions, domain-specific conventions, or example "
        "snippets from a runbook. Inputs: name (the skill "
        "name from the system prompt list)."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Skill name from the 'Available skills' "
                    "section of the system prompt. e.g. "
                    "`web_lookup`."
                ),
            },
        },
        "required": ["name"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.err("name is required")
        if not _NAME_RE.match(name):
            return ToolResult.err(f"invalid skill name {name!r}")
        board = self.bus.board(GetSkillJob)
        if board is None:
            return ToolResult.err("skills are not available")
        result = await asyncio.to_thread(
            lambda: board.publish(GetSkillJob(publisher="tools", name=name))
        )
        if result is None or result.status is not JobStatus.COMPLETED:
            return ToolResult.err(
                result.error if result is not None and result.error else "failed to read skill"
            )
        if result.content is None:
            return ToolResult(
                content=(
                    f"no skill named {name!r} is registered. "
                    "Available skills are listed in the system prompt."
                )
            )
        return ToolResult(content=result.content)


__all__ = ["LoadSkillTool"]
