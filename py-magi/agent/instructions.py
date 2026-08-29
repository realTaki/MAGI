"""Runtime rendering for personal, MAGIS, and role instructions — bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from old_bus import Bus

logger = logging.getLogger("agent.instructions")


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


def runtime_instruction_block(bus: Bus, *, magi_id: int | None = None) -> str:
    """Load this MAGI's instruction from MAGIS Books.

    ``magi_id`` is the runtime's own ``magis_memberships.id`` —
    propagated in from the provisioned ``RuntimeSpec`` at boot
    (:mod:`startup.runtime` → :class:`WorkerRegistry` →
    :class:`AgentWorker`). When provided, the per-MAGI
    memberships and joined MAGIS/role instructions are
    materialised through
    :meth:`bus.firmwares.books.magis.membershipBook.MagisMembershipBook.instruction_context`
    (which performs the ``magis_memberships × magis_roles × magis``
    JOIN in one query).

    When ``magi_id`` is ``None`` — tests, pre-bootstrap, or
    out-of-band callers — only the personal instruction from
    ``settings_book["instruction"]`` is rendered. The MAGIS/role
    sections are skipped, so this function can never raise on a
    startup that hasn't been MAGIS-registered yet.
    """
    try:
        if bus.memberships_book is None:
            return ""

        personal = ""
        settings = getattr(bus, "settings_book", None)
        if settings is not None:
            try:
                raw = settings.get_value(key="instruction")
                if raw:
                    personal = raw
            except Exception:
                personal = ""

        memberships: list[dict[str, Any]] = []
        if magi_id is not None and bus.memberships_book is not None:
            try:
                _, joined = bus.memberships_book.instruction_context(magi_id=magi_id)
            except Exception:
                logger.exception("instruction_context lookup failed for magi_id=%s", magi_id)
                joined = None
            for entry in joined or []:
                if not isinstance(entry, dict):
                    continue
                memberships.append(
                    {
                        "magis_name": entry.get("magis_name"),
                        "team_instruction": entry.get("team_instruction"),
                        "role_name": entry.get("role_name"),
                        "role_instruction": entry.get("role_instruction"),
                    }
                )

        return _render(personal, memberships)
    except Exception:
        logger.exception("could not load runtime instructions")
        return ""


__all__ = ["runtime_instruction_block"]
