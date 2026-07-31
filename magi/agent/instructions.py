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
        from sqlalchemy import select
        import os
        from magi.agent.db import MAGIC, MAGIS, MAGISMembership, MAGISRole
        from magi.agent.db.magis import open_magis_session
        with open_magis_session() as session:
            runtime_id = os.environ.get("MAGI_RUNTIME_ID")
            if runtime_id and runtime_id.isdigit():
                magic = session.get(MAGIC, int(runtime_id))
            else:
                root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
                magic = session.get(MAGIC, root.adam_id) if root and root.adam_id else None
            if magic is None:
                return ""
            row = session.execute(
                select(MAGISMembership, MAGISRole, MAGIS)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
                .where(MAGISMembership.magic_id == magic.id)
                .order_by(MAGISMembership.id)
            ).first()
            memberships = ([{"magis_name": row[2].name, "team_instruction": row[2].instruction, "role_name": row[1].name, "role_instruction": row[1].instruction}] if row else [])
            return _render(magic.instruction, memberships)
    except Exception:
        logger.exception("could not load runtime instructions")
        return ""


__all__ = ["runtime_instruction_block"]
