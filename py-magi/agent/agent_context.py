"""Actor-owned prompt and context construction — bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bus import Bus

logger = logging.getLogger("agent.agent_context")


def build_messages_from_conversation(
    contact_id: int | None,
    conversation_id: int | None,
    new_user_text: str,
    *,
    bus: Bus,
) -> list[dict]:
    """Load conversation history from conversations_book/messages_book."""
    if not conversation_id or contact_id is None:
        return [{"role": "user", "content": new_user_text}]

    try:
        conversation = bus.conversations_book.get_for_owner(
            contact_id=contact_id, conversation_id=conversation_id
        )
        if conversation is None:
            return [{"role": "user", "content": new_user_text}]
        msgs = bus.messages_book.list_for_conversation(conversation_id=conversation_id)
        result: list[dict] = []
        # Prepend the cumulative compaction summary, if any. Same
        # "[Prior conversation summary]" prefix used by `maybe_compact`
        # so the round-trip is stable across turns.
        if getattr(conversation, "summary", None):
            result.append(
                {
                    "role": "user",
                    "content": f"[Prior conversation summary]\n{conversation.summary}",
                }
            )
        result.extend(
            {
                "role": "user" if getattr(m, "role", "") in ("user", "system") else "assistant",
                "content": getattr(m, "text", ""),
            }
            for m in msgs
        )
        if not result or result[-1]["content"] != new_user_text:
            result.append({"role": "user", "content": new_user_text})
        return result
    except Exception:
        logger.warning("build_messages_from_conversation failed, starting fresh", exc_info=True)
        return [{"role": "user", "content": new_user_text}]


__all__ = [
    "build_messages_from_conversation",
]
