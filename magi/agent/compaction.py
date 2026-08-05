"""Auto-compaction for long chat sessions (D.17).

Kept separate from :mod:`magi.agent.token_usage` for the same
size-budget reason: prompt building, compaction, and token
accounting do not belong in the single provider-step implementation.

Two surfaces pinned:

  - :func:`maybe_compact` — entry called before a provider
    step. Estimates the in-memory ``messages`` token cost;
    if over the configured threshold, calls the LLM for a
    summary and rewrites the on-disk session.
  - :func:`call_llm_for_summary` — the compression LLM call.
    ``None`` on any failure so the caller falls through.
  - :func:`chat_to_session_message` — converts runtime
    message shape to persisted ``SessionMessage``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.providers import ChatMessage, get_provider
from magi.providers import estimate_messages_tokens
from magi.bus.protocols.session import SessionMessage, new_session_id, utcnow_iso

if TYPE_CHECKING:
    pass

logger = logging.getLogger("magi.agent.compaction")


async def maybe_compact(
    uid: int,
    session_id: str | None,
    messages: list["ChatMessage"],
) -> None:
    """Estimate token cost of ``messages``. If over the
    configured threshold, run one compaction pass.

    No-op when there's no session yet.
    """
    if not session_id:
        return

    from magi.bus import get_bus

    context_window, threshold_pct, keep = get_bus().settings.compaction_policy()
    if len(messages) <= keep:
        return

    total = estimate_messages_tokens(messages)
    threshold = context_window * threshold_pct // 100
    if total <= threshold:
        return

    to_archive = messages[:-keep]

    summary_text = await call_llm_for_summary(to_compress=to_archive)
    if not summary_text:
        logger.warning(
            "compact: no summary produced; session will retry "
            "next turn (messages=%d, total_tokens~%d)",
            len(messages), total,
        )
        return

    summary_msg = ChatMessage(
        role="user",
        content=f"[Prior conversation summary]\n{summary_text}",
    )

    store = get_bus().session
    sess = store.get(uid, session_id)
    if sess is None:
        return
    sess.archive.extend(chat_to_session_message(m) for m in to_archive)
    sess.last_compaction_at = utcnow_iso()
    sess.active_tail_count = keep
    sess.messages = [chat_to_session_message(summary_msg)] + [
        chat_to_session_message(m) for m in messages[-keep:]
    ]
    try:
        store.replace_compacted(sess, bump_updated=False)
    except Exception:
        logger.exception(
            "compact: persist failed (session=%s); in-memory "
            "messages already shrunk, on-disk archive NOT written.",
            session_id,
        )

    summary_msg_for_llm = ChatMessage(
        role="user",
        content=f"[Prior conversation summary]\n{summary_text}",
    )
    messages[:] = [summary_msg_for_llm] + messages[-keep:]


async def call_llm_for_summary(
    *,
    to_compress: list["ChatMessage"],
    wait_seconds: float = 30.0,
) -> str | None:
    """One LLM call to compress ``to_compress`` into a summary.

    Phase D — the call is published onto the providers queue and we
    wait for the result back via :meth:`BusStore.load_llm_job_result`.
    Returns the summary text, or ``None`` on any failure / timeout so
    the caller falls through without rewriting the session.
    """
    from magi.prompts import load_compaction_prompt

    system = load_compaction_prompt()
    user_lines: list[str] = []
    for m in to_compress:
        who = m.role.upper()
        user_lines.append(f"[{who}]\n{m.content}")
    user_content = "\n\n".join(user_lines)
    if len(user_content) > 6000:
        return None

    import asyncio
    import uuid as _uuid
    from magi.bus import get_bus_store
    from magi.bus.hooks.contracts import (
        HookContext,
        HookDataClassification,
        PrincipalType,
    )
    from magi.bus.protocols.llm_jobs import LLMJob
    from magi.providers.worker import enqueue_llm_job

    run_id = f"compact-{_uuid.uuid4().hex}"
    hook_context = HookContext(
        requested_by="agent.compaction",
        principal_type=PrincipalType.SYSTEM,
        principal_id="compaction",
        role=None,
        source_type="compaction",
        source_id=run_id,
        run_id=run_id,
        data_classification=HookDataClassification.INTERNAL,
    )
    job = LLMJob(
        attempt_id="",  # assigned by enqueue_llm_job
        run_id=run_id,
        inbox_event_id=None,
        kind="auto_compact",
        system=system,
        messages=({"role": "user", "content": user_content},),
        max_tokens=1024,
        tools=None,
        streaming=False,
        extra={},
        hook_context=hook_context,
    )
    attempt_id = await enqueue_llm_job(job)
    store = get_bus_store()
    result = await asyncio.to_thread(
        store.load_llm_job_result,
        attempt_id,
        wait_seconds=wait_seconds,
        poll_seconds=0.1,
    )
    if result is None:
        logger.warning("compact: provider job %s timed out", attempt_id)
        return None
    if result["status"] != "completed":
        logger.warning(
            "compact: provider job %s failed: %s",
            attempt_id, result.get("error", {}).get("detail"),
        )
        return None
    text = (result["response"].get("text") or "").strip()
    return text or None


def chat_to_session_message(m: "ChatMessage") -> SessionMessage:
    role = m.role if m.role in ("user", "assistant", "system") else "user"
    return SessionMessage(
        role=role,
        text=m.content,
        ts=utcnow_iso(),
        message_id=new_session_id(),
    )


__all__ = [
    "maybe_compact",
    "call_llm_for_summary",
    "chat_to_session_message",
]
