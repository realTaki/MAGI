"""``load_skill`` tool — LLM-callable skill body fetcher.

The tool is the second half of the skill injection. The
LLM sees the frontmatter list in the system prompt and,
when it needs more than a one-liner, calls
``load_skill(name=…)`` to fetch the markdown body.

Body size cap
-------------
We cap tool output at **32 KB**. The agent loop
truncates at 8 KB regardless (see ``agent.py``:642-645);
the difference is the *operator-visible* content: an LLM
that sees a truncation marker can decide to ask for a
specific section next turn.

Errors → ``is_error=True``
-------------------------
Missing skill → the LLM gets a friendly "did not find"
message; we don't ``is_error=True`` because the lookup
itself didn't fail — just the search came up empty.
Path-traversal attempts → ``is_error=True`` (the
LLM shouldn't have been able to read arbitrary files
under the workspace in the first place, but we
defend anyway).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from magi.agent.tools.base import Tool, ToolContext, ToolResult
from magi.agent.tools.skill_loader import (
    _process_skill_paths,
    _skill_root_dir_line,
    get_skill_loader,
)

logger = logging.getLogger("magi.agent.skills.loader_tool")

# Same name regex the loader enforces. Anyone calling
# the tool with a name we wouldn't have accepted at
# load time gets a clear error rather than a silent miss.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")

# Cap on body size; see module docstring.
_BODY_MAX_BYTES = 32 * 1024


def _read_skill_body(path, skill_dir: Path) -> str:
    """Read + truncate + path-rewrite the skill body.

    Three steps:

      1. Read the file, byte-truncate at
         ``_BODY_MAX_BYTES`` (UTF-8 safe — we never
         slice inside a multi-byte rune). The
         frontmatter is stripped here — the LLM
         already saw ``name``/``description`` in the
         system-prompt block, so re-emitting the
         raw frontmatter is noise.
      2. Rewrite relative file references in the body
         to absolute paths via
         :func:`_process_skill_paths` (Progressive
         Disclosure Level 3).
      3. Prepend the "Skill Root Directory" line so the
         LLM knows where sibling files live.
    """
    raw = path.read_bytes()
    truncated_marker = ""
    if len(raw) > _BODY_MAX_BYTES:
        # Truncate at byte boundary, then add a marker
        # so the LLM knows there's more it cannot see.
        truncated = raw[:_BODY_MAX_BYTES]
        # Walk back to the start of the last code point so
        # the truncated string is valid utf-8.
        while truncated and (truncated[-1] & 0xC0) == 0x80:
            truncated = truncated[:-1]
        raw = truncated
        truncated_marker = (
            f"\n\n…[truncated at {_BODY_MAX_BYTES} bytes; the rest of "
            f"the skill is unavailable through this tool]"
        )
    text = raw.decode("utf-8", errors="replace")
    # Strip the YAML frontmatter. The closing ``---``
    # is the second ``---`` line; everything after is
    # the body. We use the same logic the loader uses
    # at registration time — keep them in sync.
    if text.startswith("---"):
        lines = text.splitlines()
        close_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                close_idx = i
                break
        if close_idx != -1:
            text = "\n".join(lines[close_idx + 1 :])
    # Run the path rewriter BEFORE prepending the root
    # line, so the rewriter doesn't accidentally rewrite
    # the absolute path we just inserted.
    text = _process_skill_paths(text, skill_dir)
    return _skill_root_dir_line(skill_dir) + text + truncated_marker


class SkillLoaderTool(Tool):
    """The ``load_skill`` tool — name `load_skill`, schema ``{name: str}``.

    Resolves the singleton at construction (not lazily)
    so a misconfigured boot is loud at import time rather
    than silent on the LLM's first call.
    """

    name = "load_skill"
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

    def __init__(self) -> None:
        # Force the singleton during tool construction so a
        # misconfigured / missing workspace surfaces here
        # rather than at first tool call.
        self._loader = get_skill_loader()

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        if not _NAME_RE.match(name):
            return ToolResult(
                content=f"invalid skill name {name!r}",
                is_error=True,
            )
        meta = self._loader.get(name)
        if meta is None:
            # The LLM might guess. ``is_error=False`` so the
            # model sees a normal "didn't find" reply and
            # can pivot to reading files directly or
            # otherwise move on.
            logger.info("load_skill: unknown skill %r", name)
            return ToolResult(
                content=(
                    f"no skill named {name!r} is registered. "
                    "Available skills are listed at the bottom of "
                    "the system prompt."
                )
            )
        # Defensive check: refuse path-traversal-y names.
        # The regex above already restricts the alphabet, but
        # double-check the resolved path is under our
        # workspace's ``skills/`` directory.
        try:
            meta.path.resolve().relative_to(
                self._loader._skills_dir.resolve()  # noqa: SLF001
            )
        except ValueError:
            logger.warning(
                "load_skill: path-traversal attempt for %r", name,
            )
            return ToolResult(
                content="invalid skill path", is_error=True,
            )
        try:
            body = _read_skill_body(meta.path, meta.path.parent)
        except OSError as exc:
            logger.warning(
                "load_skill: %s read failed: %s", meta.path, exc,
            )
            return ToolResult(
                content=f"failed to read skill body: {exc}",
                is_error=True,
            )
        return ToolResult(content=body)


__all__ = ["SkillLoaderTool"]
