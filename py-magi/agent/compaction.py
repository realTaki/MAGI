"""Pure token estimates used by :mod:`agent.worker`'s BUS-backed compaction."""

from __future__ import annotations

from bus import LLMMessage

TOKENS_PER_MESSAGE_OVERHEAD = 4


def estimate_string_tokens(value: str | None) -> int:
    """A deliberately cheap, provider-neutral token estimate."""
    return max(0, len(value or "") // 4)


def estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    return sum(TOKENS_PER_MESSAGE_OVERHEAD + estimate_string_tokens(message.content) for message in messages)
