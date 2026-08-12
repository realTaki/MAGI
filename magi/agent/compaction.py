"""Auto-compaction for long chat conversations — bus only.

Incremental: each pass folds the previous ``Conversation.summary`` plus
the about-to-be-archived message tail into a fresh summary, persists
it via :meth:`ConversationBook.set_summary`, and flips the rolled-out
rows' ``archived`` flag via :meth:`MessageBook.archive`. The keep-tail
messages stay as raw active rows so the LLM always sees the most
recent turns in full.

Skipped when:
  - conversation_id is missing
  - len(messages) <= keep_tail
  - total tokens (summary + history) under threshold_pct of context_window

On LLM failure or persistence failure we return ``None`` so the caller
falls back to the dict list it already has — the turn must not fail
just because compaction failed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.agent.tokens import (
    TOKENS_PER_MESSAGE_OVERHEAD,
    estimate_messages_tokens,
    estimate_string_tokens,
)

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.library.local.conversationBook import Message

logger = logging.getLogger("magi.agent.compaction")

_DEFAULT_COMPACTION_KEEP = 8
_DEFAULT_CONTEXT_WINDOW = 200_000
_DEFAULT_THRESHOLD_PCT = 80

# Cap on the joined "prior summary + to-archive" payload sent to the LLM.
# Past this, retry with truncated summary; still too long → skip + log.
_COMPRESS_INPUT_CAP = 12_000
# Truncation budget when retrying: keep head + tail of prior summary.
_TRUNCATE_HEAD = 2_000
_TRUNCATE_TAIL = 2_000


def _dto_to_dict(m: "Message") -> dict:
    """Mirror ``build_messages_from_conversation``'s role mapping."""
    role = m.role if m.role in ("user", "system") else "assistant"
    return {"role": role, "content": m.text}


def _format_user_content(*, prior_summary: str | None, to_archive: list["Message"]) -> str:
    """Build the LLM input: prior summary (if any) + each to-archive message."""
    parts: list[str] = []
    if prior_summary:
        parts.append(f"[Prior summary]\n{prior_summary}")
    for m in to_archive:
        parts.append(f"[{m.role.upper()}]\n{m.text}")
    return "\n\n".join(parts)


async def maybe_compact(
    contact_id: int,
    conversation_id: str | None,
    message_dtos: list["Message"],
    *,
    bus,
) -> list[dict] | None:
    """Estimate token cost. If over threshold, run one compaction pass.

    Returns the new in-context dict list (1 summary + tail) on success,
    or ``None`` to signal "no change" (caller keeps its existing list).
    """
    if not conversation_id:
        return None

    try:
        keep_raw = bus.settings_book.get(key="compaction.keep_tail")
        keep = int(keep_raw) if keep_raw else _DEFAULT_COMPACTION_KEEP
        window_raw = bus.settings_book.get(key="compaction.context_window")
        context_window = int(window_raw) if window_raw else _DEFAULT_CONTEXT_WINDOW
        pct_raw = bus.settings_book.get(key="compaction.threshold_pct")
        threshold_pct = int(pct_raw) if pct_raw else _DEFAULT_THRESHOLD_PCT
    except Exception:
        context_window, threshold_pct, keep = (
            _DEFAULT_CONTEXT_WINDOW,
            _DEFAULT_THRESHOLD_PCT,
            _DEFAULT_COMPACTION_KEEP,
        )

    if len(message_dtos) <= keep:
        return None

    sess = bus.conversations_book.get_for_owner(
        contact_id=contact_id, conversation_id=conversation_id
    )
    if sess is None:
        return None

    prior_summary = sess.summary
    summary_tokens = estimate_string_tokens(prior_summary or "") + TOKENS_PER_MESSAGE_OVERHEAD
    history_tokens = estimate_messages_tokens([_dto_to_dict(m) for m in message_dtos])
    threshold = context_window * threshold_pct // 100
    if summary_tokens + history_tokens <= threshold:
        return None

    tail = message_dtos[-keep:]
    to_archive = message_dtos[:-keep]

    user_content = _format_user_content(prior_summary=prior_summary, to_archive=to_archive)
    new_summary = await call_llm_for_summary(
        to_compress=user_content,
        contact_id=contact_id,
        conversation_id=conversation_id,
        bus=bus,
    )
    if not new_summary:
        logger.warning(
            "compact: no summary (messages=%d, tokens~%d)",
            len(message_dtos),
            summary_tokens + history_tokens,
        )
        return None

    # Persist: summary first (the new canonical state), then archive
    # rolled-out rows. Sync calls inside async function — fine.
    try:
        bus.conversations_book.set_summary(
            contact_id=contact_id,
            conversation_id=conversation_id,
            summary=new_summary,
        )
    except Exception:
        logger.exception("compact set_summary failed (conversation=%s)", conversation_id)
        # Don't fail the turn on persistence failure; just skip the archive too.
        return None

    for m in to_archive:
        try:
            bus.messages_book.archive(message_id=m.id)
        except Exception:
            logger.exception(
                "compact archive failed (conversation=%s message_id=%s)",
                conversation_id,
                m.id,
            )
            # Continue with the rest — partial archive is better than none.

    return [
        {"role": "user", "content": f"[Prior conversation summary]\n{new_summary}"}
    ] + [_dto_to_dict(m) for m in tail]


async def call_llm_for_summary(
    *,
    to_compress: str,
    contact_id: int | None = None,
    conversation_id: str | None = None,
    wait_seconds: float = 30.0,
    bus,
) -> str | None:
    """One LLM call to compress *to_compress* (already-joined string) into a summary.

    If the payload is over ``_COMPRESS_INPUT_CAP``, retry once with the
    prior summary truncated to head+tail; still too long → return
    ``None`` (caller skips this compaction pass).
    """
    prompt_book = bus.prompt_book
    if prompt_book is None:
        return None

    system = prompt_book.compaction_prompt()

    if len(to_compress) > _COMPRESS_INPUT_CAP:
        # Try truncating the "[Prior summary]\n..." prefix if it's there
        marker = "[Prior summary]\n"
        if to_compress.startswith(marker):
            tail_start = _COMPRESS_INPUT_CAP - _TRUNCATE_TAIL
            head_end = marker + _TRUNCATE_HEAD  # i.e. first _TRUNCATE_HEAD chars after marker
            truncated = to_compress[:head_end] + "\n[…truncated…]\n" + to_compress[tail_start:]
            if len(truncated) <= _COMPRESS_INPUT_CAP:
                to_compress = truncated
            else:
                return None
        else:
            return None

    from magi.bus.guild.callLLMJob import CallLLMJob

    job = CallLLMJob(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": to_compress},
        ],
        max_tokens=1024,
        parameters={
            "phase": "auto_compact",
            "contact_id": contact_id,
            "conversation_id": conversation_id,
        },
    )
    key = bus.llm_job_board.publish(job)
    result = await bus.llm_job_board.wait_for_result(key=key, timeout=wait_seconds)
    if result is None:
        logger.warning("compact: provider job timed out")
        return None
    if not result.success:
        logger.warning("compact: provider job failed: %s", getattr(result, "error", "?"))
        return None
    resp = getattr(result, "response", None) or {}
    text = (resp.get("text") or "").strip()
    return text or None


__all__ = ["maybe_compact", "call_llm_for_summary"]