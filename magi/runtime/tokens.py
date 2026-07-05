"""Local token estimator for the compaction trigger."""

from __future__ import annotations

import json

from magi.runtime.llm.provider import ChatMessage

CHARS_PER_TOKEN = 4
TOKENS_PER_MESSAGE_OVERHEAD = 4


def estimate_messages_tokens(messages):
    """Rough token count for a list of messages."""
    chars = 0
    for m in messages:
        chars += len(m.content or "")
        if m.content_blocks:
            chars += len(json.dumps(m.content_blocks, ensure_ascii=False))
    text_tokens = chars // CHARS_PER_TOKEN
    overhead = sum(1 for _ in messages) * TOKENS_PER_MESSAGE_OVERHEAD
    return text_tokens + overhead


def estimate_string_tokens(s):
    """Same heuristic for a free-form string."""
    return len(s) // CHARS_PER_TOKEN