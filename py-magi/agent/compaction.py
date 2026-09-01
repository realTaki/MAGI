"""Pure context-budget and tool-round shaping helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass, replace
from typing import Any

from bus import LLMMessage, LLMMessageRole, LLMTool

TOKENS_PER_MESSAGE_OVERHEAD = 4


@dataclass(frozen=True)
class ToolRound:
    """One complete LLM tool-call exchange and its optional next user input."""

    assistant: LLMMessage
    results: tuple[LLMMessage, ...]
    pending: LLMMessage | None = None

    def messages(self) -> tuple[LLMMessage, ...]:
        return (
            self.assistant,
            *self.results,
            *((self.pending,) if self.pending is not None else ()),
        )

    def without_tools(self) -> tuple[LLMMessage, ...]:
        """Keep conversational text while removing one obsolete tool exchange."""
        assistant = replace(self.assistant, tool_calls=None, thinking_blocks=None)
        return (assistant, *((self.pending,) if self.pending is not None else ()))


def estimate_string_tokens(value: str | None) -> int:
    """A deliberately cheap, provider-neutral token estimate."""
    return max(0, len(value or "") // 4)


def estimate_messages_tokens(messages: list[LLMMessage] | tuple[LLMMessage, ...]) -> int:
    return sum(
        TOKENS_PER_MESSAGE_OVERHEAD
        + estimate_string_tokens(message.content)
        + estimate_value_tokens(message.tool_calls)
        for message in messages
    )


def estimate_tools_tokens(tools: list[LLMTool]) -> int:
    return estimate_value_tokens(tools)


def estimate_value_tokens(value: Any) -> int:
    if value is None:
        return 0

    def default(item: Any):
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, set):
            return sorted(item)
        return str(item)

    return estimate_string_tokens(json.dumps(value, ensure_ascii=False, default=default))


def compact_source_messages(
    messages: list[LLMMessage] | tuple[LLMMessage, ...],
) -> tuple[LLMMessage, ...]:
    """Dialogue only. Tool calls and results stay in the live two-round cache."""
    kept: list[LLMMessage] = []
    for message in messages:
        if message.role is LLMMessageRole.TOOL:
            continue
        if not (message.content or "").strip():
            continue
        if message.tool_calls or message.thinking_blocks:
            kept.append(replace(message, tool_calls=None, thinking_blocks=None))
            continue
        kept.append(message)
    return tuple(kept)


def tool_rounds_messages(rounds: tuple[ToolRound, ...]) -> tuple[LLMMessage, ...]:
    return tuple(message for round_ in rounds for message in round_.messages())


def trim_tool_rounds(
    rounds: tuple[ToolRound, ...],
    *,
    keep: int = 2,
) -> tuple[tuple[ToolRound, ...], tuple[LLMMessage, ...]]:
    """Keep recent complete exchanges and return text retained from older rounds."""
    if len(rounds) <= keep:
        return rounds, ()
    expired, retained = rounds[:-keep], rounds[-keep:]
    return retained, tuple(message for round_ in expired for message in round_.without_tools())


__all__ = [
    "TOKENS_PER_MESSAGE_OVERHEAD",
    "ToolRound",
    "compact_source_messages",
    "estimate_messages_tokens",
    "estimate_string_tokens",
    "estimate_tools_tokens",
    "estimate_value_tokens",
    "tool_rounds_messages",
    "trim_tool_rounds",
]
