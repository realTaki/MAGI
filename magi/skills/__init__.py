"""Skills — the lightweight prompt-content surface.

Skills are directories containing ``SKILL.md`` files on disk (under
``workspace/skills/`` and ``magi/skills/``).  The loader in
:mod:`magi.skills.loader` handles the full file I/O; this package
exposes only the skill metadata list for ``system_prompt.py``.

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
