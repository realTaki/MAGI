"""Shared runtime types — pure data, zero side-effects.

These dataclasses are deliberately importable by *every* package
(agent, tools, channels, bus, db) without creating circular imports
or pulling in SQLAlchemy, FastAPI, or any LLM provider.

``ToolContext`` and ``ToolResult`` were moved here from
:mod:`magi.tools.base` so the agent loop can reference them
without importing from the tools package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magi.channels import Channel


@dataclass(frozen=True)
class ToolContext:
    """Per-call state passed to every tool.

    Frozen so a tool can't accidentally mutate the context
    mid-run.
    """

    state_dir: str
    workspace: Path
    uid: int
    channel: Channel | str
    # The chat session's persisted id (``chat_sessions.session_id``).
    # Empty string when there's no session-bound chat.
    session_id: str = ""


@dataclass
class ToolResult:
    """What a tool returns to the agent loop.

    ``content`` is what the LLM sees next turn (as a
    ``tool_result`` block). ``is_error=True`` tells the LLM
    "this didn't work, here's why; pick a different
    approach".
    """

    content: str
    is_error: bool = False


# Re-export from the old location so ``from magi.tools.base import
# ToolContext, ToolResult`` still works during the transition.
# New code should import from ``magi.types`` directly.
