"""Pure conversion from Firmware message DTOs to the LLM conversation contract."""

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


__all__ = ["messages_from_records"]
