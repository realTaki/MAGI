"""Actor-owned prompt and context construction — bus only."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.agent.agent_context")

DEFAULT_MAX_TOKENS = 1024


@dataclass
class AgentContext:
    soul: str
    tool_schemas: list[dict]
    messages: list[dict]


def build_messages_from_session(
    uid: int | None,
    session_id: str | None,
    new_user_text: str,
    *,
    bus: "Bus",
) -> list[dict]:
    """Load session history from sessions_book/messages_book."""
    if not session_id or uid is None:
        return [{"role": "user", "content": new_user_text}]

    try:
        session = bus.sessions_book.get_for_owner(uid=uid, session_id=session_id)
        if session is None:
            return [{"role": "user", "content": new_user_text}]
        msgs = bus.messages_book.list_for_session(session_id=session_id)
        result = [
            {"role": "user" if getattr(m, "role", "") in ("user", "system") else "assistant",
             "content": getattr(m, "text", "")}
            for m in msgs
        ]
        if not result or result[-1]["content"] != new_user_text:
            result.append({"role": "user", "content": new_user_text})
        return result
    except Exception:
        logger.warning("build_messages_from_session failed, starting fresh", exc_info=True)
        return [{"role": "user", "content": new_user_text}]


def build_context(
    *,
    text: str,
    channel: str,
    uid: int | None,
    session_id: str | None,
    caller_role: str | None,
    bus: "Bus",
) -> AgentContext | None:
    try:
        schemas = bus.tool_definitions_book.list_enabled(caller_role=caller_role)
        tool_schemas = [
            {"name": d.name, "description": d.description, "input_schema": d.input_schema}
            for d in (schemas or [])
        ]
    except Exception:
        logger.warning("tool schemas load failed", exc_info=True)
        tool_schemas = []

    return AgentContext(
        soul="",  # caller provides via build_system_prompt
        tool_schemas=tool_schemas,
        messages=build_messages_from_session(uid, session_id, text, bus=bus),
    )


__all__ = [
    "AgentContext",
    "DEFAULT_MAX_TOKENS",
    "build_context",
    "build_messages_from_session",
]
