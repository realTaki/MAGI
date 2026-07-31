"""Runtime rendering for personal, MAGIS, and role instructions."""

from __future__ import annotations

import json
import logging
import os
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
    """Load the instruction bundle for either EVE or the root Adam runtime."""
    raw = os.environ.get("MAGI_INSTRUCTION_BUNDLE")
    if raw:
        try:
            bundle = json.loads(raw)
            return _render(str(bundle.get("personal_instruction") or ""), list(bundle.get("memberships") or []))
        except (ValueError, TypeError):
            logger.warning("invalid MAGI_INSTRUCTION_BUNDLE; ignoring it")
            return ""

    # Adam shares its control-plane DB. EVE does not, so it always receives
    # the frozen startup bundle above.
    try:
        from sqlalchemy import select
        from magi.agent.db import MAGIC, MAGIS, MAGISMembership, MAGISRole, open_session
        with open_session() as session:
            root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            magic = session.get(MAGIC, root.adam_id) if root and root.adam_id else None
            if magic is None:
                return ""
            rows = session.execute(
                select(MAGISMembership, MAGISRole, MAGIS)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
                .where(MAGISMembership.magic_id == magic.id)
                .order_by(MAGISMembership.id)
            ).all()
            memberships = [
                {"magis_name": society.name, "team_instruction": society.instruction, "role_name": role.name, "role_instruction": role.instruction}
                for _membership, role, society in rows
            ]
            return _render(magic.instruction, memberships)
    except Exception:
        logger.exception("could not load runtime instructions")
        return ""


__all__ = ["runtime_instruction_block"]
