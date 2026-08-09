"""Runtime rendering for personal, MAGIS, and role instructions — new_bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.new_bus import NewBus

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
        "If they conflict irreconcilably, explain the conflict to the MAGIS ADAM or administrator instead of silently choosing one.\n\n"
        + "\n\n".join(parts)
    )


def runtime_instruction_block(bus: "NewBus") -> str:
    """Load this MAGI's instruction from MAGIS Books."""
    try:
        if bus.memberships_book is None:
            return ""

        personal = ""
        settings = getattr(bus, "settings_book", None)
        if settings is not None:
            raw = settings.get(key="instruction")
            if raw:
                personal = raw

        memberships: list[dict[str, Any]] = []
        try:
            rows = bus.memberships_book.list_all()
            for row in rows or []:
                memberships.append({
                    "magis_name": getattr(row, "magis_name", None),
                    "team_instruction": getattr(row, "team_instruction", None),
                    "role_name": getattr(row, "role_name", None),
                    "role_instruction": getattr(row, "role_instruction", None),
                })
        except Exception:
            pass
        return _render(personal, memberships)
    except Exception:
        logger.exception("could not load runtime instructions")
        return ""


__all__ = ["runtime_instruction_block"]
