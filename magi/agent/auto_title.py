"""Agent-owned background job: generate a 3-5-word chat title — new_bus only."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.new_bus import NewBus

logger = logging.getLogger("magi.agent.auto_title")


async def request_session_title(
    uid: int,
    session_id: str,
    *,
    bus: "NewBus",
) -> str | None:
    """Generate + persist a short chat title; return it."""
    from magi.prompts import load_chat_title_prompt

    try:
        sess = bus.sessions_book.get_for_owner(uid=uid, session_id=session_id)
    except Exception:
        return None
    if sess is None or getattr(sess, "title", None) is not None:
        return None

    msgs = bus.messages_book.list_for_session(session_id=session_id)
    first_user = next(
        (m for m in msgs if getattr(m, "role", "") == "user" and getattr(m, "text", "")),
        None,
    )
    if first_user is None:
        return None

    from magi.new_bus.guild.callLLMJob import CallLLMJob

    job = CallLLMJob(
        messages=(
            {"role": "system", "content": load_chat_title_prompt()},
            {"role": "user", "content": getattr(first_user, "text", "")},
        ),
        max_tokens=20,
        parameters={"uid": uid, "session_id": session_id, "phase": "auto_title"},
    )
    key = bus.llm_job_board.publish(job)
    result = await bus.llm_job_board.wait_for_result(key=key, timeout=30.0)
    if result is None or not result.success:
        return None
    resp = getattr(result, "response", None) or {}
    cleaned = _cleanse_title(resp.get("text") or "")
    if not cleaned:
        return None

    try:
        fresh = bus.sessions_book.set_title_if_null(
            uid=uid, session_id=session_id, title=cleaned,
        )
    except Exception:
        return None
    if fresh is None:
        return None
    logger.info("title set session=%s title=%r", session_id, cleaned)
    return cleaned


def _cleanse_title(raw: str) -> str:
    lines = [
        ln.strip().strip('"\'""''`')
        for ln in raw.strip().splitlines()
        if ln.strip()
    ]
    if not lines:
        return ""
    return lines[0][:80]


__all__ = ["request_session_title"]
