"""Agent-owned background job: generate a 3-5-word chat title — bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.bus.guild.base import JobStatus

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.agent.auto_title")


async def request_conversation_title(
    contact_id: int,
    conversation_id: int,
    *,
    bus: Bus,
) -> str | None:
    """Generate + persist a short chat title; return it."""
    try:
        sess = bus.conversations_book.get_for_owner(
            contact_id=contact_id, conversation_id=conversation_id
        )
    except Exception:
        return None
    if sess is None or getattr(sess, "title", None) is not None:
        return None

    msgs = bus.messages_book.list_for_conversation(conversation_id=conversation_id)
    first_user = next(
        (m for m in msgs if getattr(m, "role", "") == "user" and getattr(m, "text", "")),
        None,
    )
    if first_user is None:
        return None

    from magi.bus.guild.callLLMJob import CallLLMJob

    job = CallLLMJob(
        messages=[
            {"role": "system", "content": bus.prompt_book.get(key="agent/chat_titles") or ""},
            {"role": "user", "content": getattr(first_user, "text", "")},
        ],
        contact_id=contact_id,
        max_tokens=20,
    )
    llm_job_id = bus.llm_job_board.publish(job)
    result = await bus.llm_job_board.wait_for_result(job_id=llm_job_id, timeout=30.0)
    if result is None or result.status != JobStatus.COMPLETED:
        return None
    resp = getattr(result, "response", None) or {}
    cleaned = _cleanse_title(resp.get("text") or "")
    if not cleaned:
        return None

    try:
        fresh = bus.conversations_book.set_title_if_null(
            contact_id=contact_id,
            conversation_id=conversation_id,
            title=cleaned,
        )
    except Exception:
        return None
    if fresh is None:
        return None
    logger.info("title set conversation=%s title=%r", conversation_id, cleaned)
    return cleaned


def _cleanse_title(raw: str) -> str:
    lines = [ln.strip().strip("\"'`") for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[0][:80]


__all__ = ["request_conversation_title"]
