"""Pure rendering for the runtime-local personal instruction."""

from __future__ import annotations


def render_instruction_block(personal_instruction: str | None) -> str:
    value = (personal_instruction or "").strip()
    if not value:
        return ""
    return (
        "# Instructions\n"
        "These instructions are part of your operating context. Try to comply with all of them. "
        "If they conflict irreconcilably, explain the conflict instead of silently choosing one.\n\n"
        "## Your personal instruction\n"
        + value
    )
