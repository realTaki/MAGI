"""Skill loader — SKILL.md → Agent.

Public surface (re-exported below):

- :class:`SkillLoader` — scans ``<workspace>/skills/*/SKILL.md``
- :func:`get_skill_loader` — module singleton accessor
- :class:`SkillMeta` — one row of the registry
- :func:`format_skills_block` — render the system-prompt
  block for ``handle_message``.

The companion ``SkillLoaderTool`` lives in
:mod:`magi.runtime.skills.loader_tool` and is registered
through the standard tool registry in :mod:`magi.runtime.tools`.
"""

from magi.runtime.skills.loader import (
    SkillLoader,
    SkillMeta,
    _reset_for_tests,
    format_skills_block,
    get_skill_loader,
)

__all__ = [
    "SkillLoader",
    "SkillMeta",
    "get_skill_loader",
    "format_skills_block",
    "_reset_for_tests",
]
