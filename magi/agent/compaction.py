"""Auto-compaction for long chat sessions (D.17).

Kept separate from :mod:`magi.agent.step` for the same
size-budget reason as :mod:`magi.agent.token_usage`: prompt
building, compaction, and token accounting do not belong in
the single provider-step implementation.

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
) -> str | None:
    """One LLM call to compress ``to_compress`` into a summary.
    Returns the summary text, or ``None`` on any failure.
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
    try:
        provider = get_provider()
        result = await provider.chat(
            system=system,
            messages=[ChatMessage(role="user", content=user_content)],
            max_tokens=1024,
        )
        text = (result.text or "").strip()
        return text or None
    except Exception:
        logger.exception("compact: LLM call failed; skipping")
        return None


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
