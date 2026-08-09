"""System prompt assembly — new_bus only."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from magi.startup.paths import resolve_workspace_dir as workspace_dir

if TYPE_CHECKING:
    from magi.new_bus import NewBus

logger = logging.getLogger("magi.agent.system_prompt")

SOUL_FILENAME = "SOUL.md"


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


def read_soul() -> str:
    from magi.prompts import load_fallback_persona

    soul_path = workspace_dir() / SOUL_FILENAME
    try:
        text = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return load_fallback_persona()
    text = text.strip()
    return text or load_fallback_persona()


def build_system_prompt(
    *,
    uid: int,
    soul: str,
    bus: "NewBus",
) -> str:
    """Assemble the full system prompt for one LLM turn.

    Six blocks: SOUL → Instructions → Memory → Contact → Daily note → Skills
    """
    from magi.prompts import load_skills_block_template

    parts: list[str] = [soul]

    # 2. Instructions
    from magi.agent.instructions import runtime_instruction_block

    instruction_block = runtime_instruction_block(bus)
    if instruction_block:
        parts.append(instruction_block)

    # 3. Memory
    try:
        rows = bus.memory_book.list_by_owner(uid=uid)
        block = _format_memory_block(rows)
    except Exception:
        logger.exception("memory block load failed for uid=%s", uid)
        block = ""
    if block:
        parts.append(block)

    # 4. Contact
    try:
        contact = bus.contacts_book.get(contact_id=uid)
        notes = bus.contact_notes_book.list_for_contact(contact_id=uid) if contact else None
        contact_block = _format_contact_block(contact, notes)
    except Exception:
        logger.exception("contact block load failed for uid=%s", uid)
        contact_block = ""
    if contact_block:
        parts.append(contact_block)

    # 5. Daily note
    try:
        note = bus.contact_notes_book.read_daily_note(contact_id=uid)
        daily_block = _format_daily_note_block(note)
    except Exception:
        logger.exception("daily note block load failed for uid=%s", uid)
        daily_block = ""
    if daily_block:
        parts.append(daily_block)

    # 6. Skills
    skills_book = getattr(bus, "skills_book", None)
    if skills_book is not None:
        try:
            metas = skills_book.list()
            if metas:
                lines = ["", *load_skills_block_template().splitlines(), ""]
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


__all__ = ["SOUL_FILENAME", "read_soul", "build_system_prompt"]
