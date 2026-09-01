"""Pure token-estimation helpers used by the BUS-backed compaction path."""

from __future__ import annotations

from agent.compaction import (
    TOKENS_PER_MESSAGE_OVERHEAD,
    estimate_messages_tokens,
    estimate_string_tokens,
)
from bus import LLMMessage, LLMMessageRole


def test_estimates_are_non_negative_and_include_message_overhead() -> None:
    message = LLMMessage(role=LLMMessageRole.USER, content="abcdefgh")
    assert estimate_string_tokens(message.content) == 2
    assert estimate_messages_tokens([message]) == TOKENS_PER_MESSAGE_OVERHEAD + 2
    assert estimate_string_tokens(None) == 0
