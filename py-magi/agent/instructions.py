"""Runtime rendering for the local personal instruction — bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from old_bus import Bus

logger = logging.getLogger("agent.instructions")


def _render(personal_instruction: str) -> str:
    parts: list[str] = []
    if personal_instruction.strip():
        parts.append("## Your personal instruction\n" + personal_instruction.strip())
    if not parts:
        return ""
    return (
        "# Instructions\n"
        "These instructions are part of your operating context. Try to comply with all of them. "
        "If they conflict irreconcilably, explain the conflict instead of silently choosing one.\n\n"
        + "\n\n".join(parts)
    )


def runtime_instruction_block(bus: Bus) -> str:
    """Load the Runtime-local personal instruction."""
    try:
        personal = ""
        settings = getattr(bus, "settings_book", None)
        if settings is not None:
            try:
                raw = settings.get_value(key="instruction")
                if raw:
                    personal = raw
            except Exception:
                personal = ""

        return _render(personal)
    except Exception:
        logger.exception("could not load runtime instructions")
        return ""


__all__ = ["runtime_instruction_block"]
