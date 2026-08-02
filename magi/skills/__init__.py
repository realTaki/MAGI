"""Skills — the lightweight prompt-content surface.

Skills are directories containing ``SKILL.md`` files on disk (under
``workspace/skills/`` and ``magi/skills/``).  The loader in
:mod:`magi.skills.loader` handles the full file I/O; this package
exposes only what ``system_prompt.py`` needs: a list of (name, description)
metas and a formatter.

The ``load_skill`` tool (which retrieves full skill bodies) lives in
:mod:`magi.skills.loader_tool`.
"""

from __future__ import annotations

from typing import Protocol


class SkillMeta(Protocol):
    """The subset of ``magi.skills.loader.SkillMeta`` that callers consume."""
    name: str
    description: str
    version: str | None


def get_skill_metas() -> list[SkillMeta]:
    """Return metadata for every discovered skill."""
    from magi.skills.loader import get_skill_loader
    return list(get_skill_loader().list())


def format_skills_block(skills: list[SkillMeta]) -> str:
    """Render an "Available skills" markdown block for the system prompt."""
    if not skills:
        return ""
    from magi.prompts import load_skills_block_template
    lines = ["", *load_skills_block_template().splitlines(), ""]
    for s in skills:
        if s.version:
            lines.append(f"- **{s.name}** (v{s.version}) — {s.description}")
        else:
            lines.append(f"- **{s.name}** — {s.description}")
    return "\n".join(lines)
