"""Actor-owned prompt and provider context construction.

This module deliberately has no tool-execution loop.  An :class:`AgentWorker`
uses it for exactly one provider call, then persists the resulting decision
before a separate tool worker performs any side effect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from magi.agent.compaction import maybe_compact
from magi.agent.llm import ChatMessage, LLMNotConfiguredError, LLMProvider, get_provider
from magi.agent.system_prompt import build_system_prompt, read_soul
from magi.bus import ToolContext, get_bus
from magi.launcher.paths import workspace_dir
from magi.prompts import load_bot_replies

logger = logging.getLogger("magi.agent.agent_context")

DEFAULT_MAX_TOKENS = 1024
_FALLBACK_REPLY_CACHE: dict[str, str] = {}


@dataclass
class AgentContext:
    provider: LLMProvider
    soul: str
    tool_ctx: ToolContext
    tool_schemas: list[dict]
    messages: list[ChatMessage]


def fallback_reply(key: str = "agent_fallback") -> str:
    cached = _FALLBACK_REPLY_CACHE.get(key)
    if cached is None:
        cached = load_bot_replies()[key]
        _FALLBACK_REPLY_CACHE[key] = cached
    return cached


def validate_credentials(*, uid: int | None, channel: str) -> bool:
    try:
        get_provider()
    except LLMNotConfiguredError as exc:
        logger.warning("agent credentials unavailable (uid=%s channel=%s): %s", uid, channel, exc)
        return False
    return True


def build_messages_from_session(
    uid: int | None, session_id: str | None, new_user_text: str,
) -> list[ChatMessage]:
    if not session_id or uid is None:
        return [ChatMessage(role="user", content=new_user_text)]
    session = get_bus().session.get(uid, session_id)
    if session is None:
        return [ChatMessage(role="user", content=new_user_text)]
    messages = [
        ChatMessage(role="user" if item.role in {"user", "system"} else "assistant", content=item.text)
        for item in session.messages
    ]
    # Channels persist the inbound first. Avoid adding the same final message
    # twice while retaining a safe fallback for non-session producers.
    if not messages or messages[-1].role != "user" or messages[-1].content != new_user_text:
        messages.append(ChatMessage(role="user", content=new_user_text))
    return messages


def build_context(
    *,
    text: str,
    channel: str,
    uid: int | None,
    session_id: str | None,
    caller_role: str | None,
) -> AgentContext | None:
    try:
        provider = get_provider()
    except LLMNotConfiguredError:
        raise
    except Exception as exc:  # malformed provider configuration is a safe fallback
        logger.warning("agent provider initialisation failed (uid=%s): %s", uid, exc)
        return None
    return AgentContext(
        provider=provider,
        soul=read_soul(),
        tool_ctx=ToolContext(
            state_dir=get_bus().settings.require_state_dir(),
            workspace=str(workspace_dir()),
            uid=uid or 0,
            channel=channel,
            session_id=session_id or "",
        ),
        # The agent sees only the durable catalog, never the tools registry.
        tool_schemas=get_bus().tool_catalog.list_schemas(caller_role=caller_role),
        messages=build_messages_from_session(uid, session_id, text),
    )


__all__ = [
    "AgentContext", "ChatMessage", "DEFAULT_MAX_TOKENS", "build_context",
    "build_messages_from_session", "build_system_prompt", "fallback_reply",
    "maybe_compact", "validate_credentials",
]
