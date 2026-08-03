"""Runtime rendering for personal, MAGIS, and role instructions."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("magi.agent.instructions")


def _render(personal_instruction: str, memberships: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if personal_instruction.strip():
        parts.append("## Your personal instruction\n" + personal_instruction.strip())
    for membership in memberships:
        society = str(membership.get("magis_name") or "Unnamed MAGIS")
        team = str(membership.get("team_instruction") or "").strip()
        role = str(membership.get("role_name") or "").strip()
        role_instruction = str(membership.get("role_instruction") or "").strip()
        if team:
            parts.append(f"## MAGIS: {society} — Team instructions\n{team}")
        if role and role_instruction:
            parts.append(f"## Your role in {society}: {role}\n{role_instruction}")
    if not parts:
        return ""
    return (
        "# Instructions\n"
        "These instructions are part of your operating context. Try to comply with all of them. "
        "If they conflict irreconcilably, explain the conflict to the MAGIS Adam or administrator instead of silently choosing one.\n\n"
        + "\n\n".join(parts)
    )


def runtime_instruction_block() -> str:
    """Load only this MAGI's direct MAGIS instruction from public database."""
    try:
        from magi.bus import bootstrap

        personal, memberships = bootstrap("").magic.instruction_context()
        return _render(personal, memberships)
    except Exception:
        logger.exception("could not load runtime instructions")
        return ""


__all__ = ["runtime_instruction_block"]
