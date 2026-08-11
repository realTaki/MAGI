"""System prompt assembly — bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.agent.system_prompt")


def _format_memory_block(rows) -> str:
    return (
        ""
        if not rows
        else "## Long-term memory\n"
        + "\n".join(
            f"- [{getattr(r, 'kind', '')}] {getattr(r, 'subject', '')}: {getattr(r, 'body', '')}"
            for r in rows
        )
    )


def _format_contact_block(contact, notes) -> str:
    if contact is None:
        return ""
    name = getattr(contact, "display_name", None) or getattr(contact, "name", "")
    lines = [f"## Current chatter\nName: {name}"]
    for note in notes or []:
        body = getattr(note, "note", "")
        if body:
            lines.append(f"- {body}")
    return "\n".join(lines)


def _format_daily_note_block(note) -> str:
    if note is None:
        return ""
    body = getattr(note, "note", None)
    return f"## Daily note\n{body}" if body else ""


def read_soul(*, bus: Bus) -> str:
    """Read the SOUL persona via ``bus.prompt_book``.

    The Book already resolves the correct SOUL source (workspace or
    bundled) via the prompts directory configured at bootstrap time.
    Falls back to the bundled fallback persona only when the canonical
    SOUL is empty.
    """
    text = bus.prompt_book.soul().strip()
    return text or bus.prompt_book.fallback_persona()


def build_system_prompt(
    *,
    contact_id: int,
    soul: str,
    bus: Bus,
    magi_id: int | None = None,
) -> str:
    """Assemble the full system prompt for one LLM turn.

    Six blocks: SOUL → Instructions → Memory → Contact → Daily note → Skills
    """
    parts: list[str] = [soul]

    # 2. Instructions
    from magi.agent.instructions import runtime_instruction_block

    instruction_block = runtime_instruction_block(bus, magi_id=magi_id)
    if instruction_block:
        parts.append(instruction_block)

    # 3. Memory
    try:
        rows = bus.memory_book.list_by_owner(contact_id=contact_id)
        block = _format_memory_block(rows)
    except Exception:
        logger.exception("memory block load failed for contact_id=%s", contact_id)
        block = ""
    if block:
        parts.append(block)

    # 4. Contact
    try:
        contact = bus.contacts_book.get(contact_id=contact_id)
        notes = bus.contact_notes_book.list_for_contact(contact_id=contact_id) if contact else None
        contact_block = _format_contact_block(contact, notes)
    except Exception:
        logger.exception("contact block load failed for contact_id=%s", contact_id)
        contact_block = ""
    if contact_block:
        parts.append(contact_block)

    # 5. Daily note
    try:
        note = bus.contact_notes_book.read_daily_note(contact_id=contact_id)
        daily_block = _format_daily_note_block(note)
    except Exception:
        logger.exception("daily note block load failed for contact_id=%s", contact_id)
        daily_block = ""
    if daily_block:
        parts.append(daily_block)

    # 6. Skills
    skills_book = getattr(bus, "skills_book", None)
    if skills_book is not None:
        try:
            metas = skills_book.list()
            if metas:
                lines = ["", *bus.prompt_book.skills_block_template().splitlines(), ""]
                for s in metas:
                    name = getattr(s, "name", "") or ""
                    desc = getattr(s, "description", "") or ""
                    ver = getattr(s, "version", None)
                    if ver:
                        lines.append(f"- **{name}** (v{ver}) — {desc}")
                    else:
                        lines.append(f"- **{name}** — {desc}")
                parts.append("\n".join(lines))
        except Exception:
            logger.exception("skills block load failed")

    rendered = "\n\n".join(parts).strip()
    return rendered or soul


__all__ = ["read_soul", "build_system_prompt"]
