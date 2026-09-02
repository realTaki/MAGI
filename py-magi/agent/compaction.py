"""Compress the summary and old chat history when they exceed a context window."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from bus import CallLLMResult, JobStatus, LLMMessage, LLMMessageRole


async def compact_messages(
    messages: list[LLMMessage],
    *,
    context_window: int,
    prompt: str,
    call_llm: Callable[..., Awaitable[CallLLMResult | None]],
) -> str | None:
    """Return a replacement summary when ``messages`` exceed the window.

    The input contains only the existing summary followed by normal chat
    history; live tool continuation state is deliberately outside this
    function.
    """
    if sum(message.estimated_tokens() for message in messages) <= context_window:
        return None

    result = await call_llm(
        [
            LLMMessage(role=LLMMessageRole.SYSTEM, content=prompt),
            *messages,
            LLMMessage(
                role=LLMMessageRole.USER,
                content="请仅总结上面的对话历史，并遵循 system 指令。",
            ),
        ],
        [],
    )
    if result is None or result.status is not JobStatus.COMPLETED or result.message is None:
        return None
    return result.message.content.strip() or None


__all__ = [
    "compact_messages",
]
