"""Token estimates used by the BUS-backed compaction path."""

from __future__ import annotations

import pytest

from agent.compaction import compact_messages
from bus import CallLLMResult, JobStatus, LLMMessage, LLMMessageRole


def test_estimates_are_non_negative_and_include_message_overhead() -> None:
    message = LLMMessage(role=LLMMessageRole.USER, content="abcdefgh")
    assert message.estimated_tokens() == 6


@pytest.mark.asyncio
async def test_compact_messages_preserves_the_recent_history_tail() -> None:
    calls = []

    async def call_llm(messages, tools, *, max_tokens):
        calls.append((messages, tools, max_tokens))
        return CallLLMResult(
            status=JobStatus.COMPLETED,
            message=LLMMessage(role=LLMMessageRole.ASSISTANT, content="summary"),
        )

    old = LLMMessage(role=LLMMessageRole.USER, content="old")
    recent = LLMMessage(role=LLMMessageRole.USER, content="recent")
    assert await compact_messages(
        [old, recent],
        context_window=1,
        keep_recent=1,
        prompt="summarize",
        max_tokens=100,
        call_llm=call_llm,
    ) == "summary"
    assert calls[0][0][1].content == "[USER]\nold"
