"""Prompt assets for the agent.

Holds the YAML-backed reply templates (``bot_replies.yaml``) plus any
small loaders the agent needs at runtime.
"""

from __future__ import annotations

import functools
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=1)
def load_bot_replies() -> dict[str, str]:
    """Load the Telegram/agent reply templates from ``bot_replies.yaml``.

    Returns a mapping of template id → ``str.format`` template string.
    The result is cached after the first read.
    """
    import yaml

    path = _PROMPTS_DIR / "bot_replies.yaml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return dict(data)


__all__ = ["load_bot_replies"]
