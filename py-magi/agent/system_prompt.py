"""Pure Agent system-prompt formatting; BUS reads belong to ``AgentWorker``."""

from __future__ import annotations


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
