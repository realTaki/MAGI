"""本地粗估 token 数 —— 给 compaction 触发器 / agent 内部判断用。

不做精确分词；用一个固定 char/token 比 + per-message overhead。
真正准确的值还得调上游 tokenizer，但那是 expensive call，触发
检查每轮都跑不合算。

住在 :mod:`magi.agent` 下而不是 :mod:`magi.providers` 下，因为这些
helper 是 agent 层（compaction）的关切，不是 LLM 调用层的关切。
provider 包只关心"怎么把请求送到模型"，不关心 agent 的内存预算。
"""

from __future__ import annotations

import json

CHARS_PER_TOKEN = 4
TOKENS_PER_MESSAGE_OVERHEAD = 4


def estimate_messages_tokens(messages) -> int:
    """Rough token count for a list of message dicts.

    Each message's ``content`` + serialized ``content_blocks`` counts
    toward the char budget; ``n_messages * TOKENS_PER_MESSAGE_OVERHEAD``
    covers role labels and structural overhead. The result is
    deliberately an estimate — callers must not assume the upstream
    tokenizer would return the same number.
    """
    chars = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        chars += len(m.get("content") or "")
        blocks = m.get("content_blocks")
        if blocks:
            chars += len(json.dumps(blocks, ensure_ascii=False))
    text_tokens = chars // CHARS_PER_TOKEN
    overhead = sum(1 for _ in messages) * TOKENS_PER_MESSAGE_OVERHEAD
    return text_tokens + overhead


def estimate_string_tokens(s: str) -> int:
    """Same heuristic for a free-form string."""
    if not s:
        return 0
    return len(s) // CHARS_PER_TOKEN


__all__ = [
    "estimate_messages_tokens",
    "estimate_string_tokens",
    "CHARS_PER_TOKEN",
    "TOKENS_PER_MESSAGE_OVERHEAD",
]
