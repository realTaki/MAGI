"""``load_skill`` tool — LLM-callable skill body fetcher.

The tool is the second half of the skill injection. The LLM sees
the frontmatter list in the system prompt and, when it needs more
than a one-liner, calls ``load_skill(name=…)`` to fetch the
markdown body.

Body size cap
-------------
We cap the tool's returned body at the same 32 KB mark
:meth:`magi.bus.firmwares.books.file.skillsBook.SkillsBook.read_body`
enforces internally — the agent loop truncates at 8 KB regardless
(``agent.py``:642-645), so anything past that is operator-visible
metadata only: an LLM that sees a truncation marker can decide to
ask for a specific section next turn.

Errors → ``is_error=True``
-------------------------
Missing skill → the LLM gets a friendly "did not find" message;
we don't ``is_error=True`` because the lookup itself didn't fail
— just the search came up empty. Bad skill names (path-traversal
attempts, empty input) → ``is_error=True``: the LLM shouldn't be
able to ask for things outside the registry.

The book itself is reached via ``ctx.bus.skills_book`` — this
tool owns no state, just like the other builtin tools
(``ReadFileTool``, ``BashRunTool``, etc.).
"""

from __future__ import annotations

import re
from typing import Any

from magi.old_bus.firmwares.books.file.skillsBook import SkillBookError
from magi.tools.base import Tool, ToolContext, ToolResult

# Same name regex the book enforces at scan time. Anyone calling
# the tool with a name we wouldn't have accepted at load time gets
# a clear error rather than a silent miss.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


class LoadSkillTool(Tool):
    """The ``load_skill`` tool — name ``load_skill``, schema ``{name: str}``.

    Visible only to ``admin`` and ``assigned`` operators — same gate
    as the WebUI dashboard and as ``ScheduleTaskTool`` / the
    action-item trio. The agent worker resolves the operator's
    role from the Contact row and filters the tool menu so
    non-eligible callers never see these tools in the LLM's menu.
    """

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

    @Tool.require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        assert ctx.bus is not None, "require_bus should have caught this"
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        if not _NAME_RE.match(name):
            return ToolResult(
                content=f"invalid skill name {name!r}",
                is_error=True,
            )
        book = ctx.bus.skills_book
        if book is None:
            return ToolResult(
                content="skills are not available (skills_book not loaded)",
                is_error=True,
            )
        meta = book.get(name)
        if meta is None:
            # The LLM might guess. ``is_error=False`` so the model
            # sees a normal "didn't find" reply and can pivot to
            # reading files directly or otherwise move on.
            return ToolResult(
                content=(
                    f"no skill named {name!r} is registered. "
                    "Available skills are listed at the bottom of "
                    "the system prompt."
                )
            )
        try:
            body = book.read_body(name)
        except SkillBookError as exc:
            return ToolResult(
                content=f"failed to read skill body: {exc}",
                is_error=True,
            )
        return ToolResult(content=body.content)


__all__ = ["LoadSkillTool"]
