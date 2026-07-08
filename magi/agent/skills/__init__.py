"""Skill loader — back-compat re-export facade.

The skill machinery moved to :mod:`magi.agent.tools`:

  - :mod:`magi.agent.tools.skill_loader`        — SkillLoader,
    get_skill_loader, format_skills_block, SkillMeta
  - :mod:`magi.agent.tools.skill_loader_tool`   — SkillLoaderTool
    (the ``load_skill`` Tool)

This module stays as a thin re-export so the existing
``from magi.agent.skills import format_skills_block, ...``
callers (agent.py / node/__init__.py / tests) don't need
to change in lockstep with the move. Treat the
``magi.agent.tools.*`` modules as canonical and this one
as a deprecated back-compat shim.
"""

from magi.agent.tools.skill_loader import (
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