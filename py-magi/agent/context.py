"""Pure Agent context construction from public BUS DTOs."""

from __future__ import annotations

from bus import LLMMessage, LLMMessageRole


def messages_from_records(*, summary: str, records) -> list[LLMMessage]:
    """Render a durable conversation snapshot without reaching into BUS Books."""
    messages: list[LLMMessage] = []
    if summary:
        messages.append(
            LLMMessage(role=LLMMessageRole.USER, content=f"[Prior conversation summary]\n{summary}")
        )
    messages.extend(
        LLMMessage(
            role=LLMMessageRole.ASSISTANT if record.contact_id == 1 else LLMMessageRole.USER,
            content=record.content,
        )
        for record in records
    )
    return messages


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


def format_system_prompt(
    *,
    soul: str,
    instruction: str,
    skills: list[str],
    memories,
    contact,
    notes,
    daily_note: str | None,
    conversation_instruction: str | None,
    conversation_info: str | None,
) -> str:
    parts = [soul]
    if instruction:
        parts.append(instruction)
    if skills:
        parts.append("## Available skills\n" + "\n".join(f"- {name}" for name in skills))
    if memories:
        parts.append(
            "## Long-term memory\n"
            + "\n".join(f"- {memory.topic}: {memory.detail}" for memory in memories)
        )
    if contact is not None:
        name = contact.nickname or contact.name or "Unknown"
        note_lines = [f"## Current chatter\nName: {name}"]
        note_lines.extend(f"- {note.note}" for note in notes if note.note)
        parts.append("\n".join(note_lines))
    if conversation_instruction:
        parts.append("## Conversation instruction\n" + conversation_instruction)
    if conversation_info:
        parts.append("## Conversation info\n" + conversation_info)
    if daily_note:
        parts.append("## Daily note\n" + daily_note)
    return "\n\n".join(part for part in parts if part).strip() or soul


__all__ = ["format_system_prompt", "messages_from_records", "render_instruction_block"]
