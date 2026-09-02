"""Pure context-budget and tool-round shaping helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass, replace
from typing import Any

from bus import LLMMessage, LLMMessageRole, LLMTool

TOKENS_PER_MESSAGE_OVERHEAD = 4


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


__all__ = [
    "TOKENS_PER_MESSAGE_OVERHEAD",
    "compact_source_messages",
    "estimate_messages_tokens",
    "estimate_string_tokens",
    "estimate_tools_tokens",
    "estimate_value_tokens",
]
