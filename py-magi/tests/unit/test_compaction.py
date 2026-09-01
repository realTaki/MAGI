"""Pure token-estimation helpers used by the BUS-backed compaction path."""

from __future__ import annotations

from agent.compaction import (
    TOKENS_PER_MESSAGE_OVERHEAD,
    compact_source_messages,
    estimate_messages_tokens,
    estimate_string_tokens,
)
from bus import LLMMessage, LLMMessageRole, LLMToolCall


def test_estimates_are_non_negative_and_include_message_overhead() -> None:
    message = LLMMessage(role=LLMMessageRole.USER, content="abcdefgh")
    assert estimate_string_tokens(message.content) == 2
    assert estimate_messages_tokens([message]) == TOKENS_PER_MESSAGE_OVERHEAD + 2
    assert estimate_string_tokens(None) == 0


def test_compact_source_keeps_dialogue_and_drops_tool_results() -> None:
    user = LLMMessage(role=LLMMessageRole.USER, content="please look this up")
    spoken = LLMMessage(role=LLMMessageRole.ASSISTANT, content="I will check.")
    tool_only = LLMMessage(
        role=LLMMessageRole.ASSISTANT,
        content="",
        tool_calls=[LLMToolCall(tool_call_id="call-1", name="lookup", arguments={"q": "x"})],
    )
    spoken_with_tools = LLMMessage(
        role=LLMMessageRole.ASSISTANT,
        content="Here is the answer.",
        tool_calls=[LLMToolCall(tool_call_id="call-2", name="lookup", arguments={"q": "y"})],
    )
    tool = LLMMessage(
        role=LLMMessageRole.TOOL,
        tool_call_id="call-1",
        content="temporary tool payload",
    )
    assert compact_source_messages(
        (user, spoken, tool_only, spoken_with_tools, tool)
    ) == (
        user,
        spoken,
        LLMMessage(role=LLMMessageRole.ASSISTANT, content="Here is the answer."),
    )
