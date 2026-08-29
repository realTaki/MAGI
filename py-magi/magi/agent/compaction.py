"""Auto-compaction for long chat conversations — bus only.

Incremental: each pass folds the previous ``Conversation.summary`` plus
the about-to-be-archived message tail into a fresh summary, persists
it via :meth:`ConversationBook.set_summary`, and flips the rolled-out
rows' ``archived`` flag via :meth:`MessageBook.archive`. The keep-tail
messages stay as raw active rows so the LLM always sees the most
recent turns in full.

Decision: **token-budget driven**, not length-driven. The trigger is
``summary_tokens + history_tokens > threshold_pct of context_window``.
When that fires we keep up to ``keep_recent`` most-recent active
messages; if even those don't fit the post-compaction budget (e.g. the
recent turns are themselves very large), we drop from the **front of
the tail** until summary + tail fits, with a hard floor of 1 (always
keep at least the most recent turn so the LLM has something to anchor
on).

Skipped when:
  - conversation_id is missing
  - total tokens (summary + history) under threshold_pct of context_window

Settings keys (must match ``magi.channels.api.system_settings`` so the
frontend "compact" panel actually controls behaviour):
  - ``system.compact_keep_recent`` — target number of recent active
    messages to keep verbatim after compaction (default 20, range
    5–100). Soft target: if N alone is still over budget, the tail
    shrinks.
  - ``system.compact_context_window`` — model context window in tokens
    (default 100_000, range 16k–200k)
  - ``system.compact_threshold_pct`` — % of the window that triggers
    compaction (default 80, range 50–95)

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
from magi.old_bus.bases.job import JobStatus

if TYPE_CHECKING:
    from magi.old_bus import Bus
    from magi.old_bus.firmwares.books.local.conversationBook import Message

logger = logging.getLogger("magi.agent.compaction")

# Defaults + bounds mirror magi.channels.api.system_settings so the code
# path stays in lockstep with the API/UI surface.
_DEFAULT_KEEP_RECENT = 20
_MIN_KEEP_RECENT = 5
_MAX_KEEP_RECENT = 100

_DEFAULT_CONTEXT_WINDOW = 100_000
_MIN_CONTEXT_WINDOW = 16_000
_MAX_CONTEXT_WINDOW = 200_000

_DEFAULT_THRESHOLD_PCT = 80
_MIN_THRESHOLD_PCT = 50
_MAX_THRESHOLD_PCT = 95

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


def _clamp_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    """Parse + clamp a settings value. Garbage / missing → default."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


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
    conversation_id: int | None,
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
        keep_raw = bus.settings_book.get_value(key="system.compact_keep_recent")
        keep = _clamp_int(keep_raw, _DEFAULT_KEEP_RECENT, _MIN_KEEP_RECENT, _MAX_KEEP_RECENT)
        window_raw = bus.settings_book.get_value(key="system.compact_context_window")
        context_window = _clamp_int(
            window_raw, _DEFAULT_CONTEXT_WINDOW, _MIN_CONTEXT_WINDOW, _MAX_CONTEXT_WINDOW
        )
        pct_raw = bus.settings_book.get_value(key="system.compact_threshold_pct")
        threshold_pct = _clamp_int(
            pct_raw, _DEFAULT_THRESHOLD_PCT, _MIN_THRESHOLD_PCT, _MAX_THRESHOLD_PCT
        )
    except Exception:
        context_window, threshold_pct, keep = (
            _DEFAULT_CONTEXT_WINDOW,
            _DEFAULT_THRESHOLD_PCT,
            _DEFAULT_KEEP_RECENT,
        )

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

    # Adaptive tail: start with the last `keep` messages; if even
    # summary + that slice is over the threshold (e.g. the recent
    # turns are themselves very large), drop from the **front of the
    # tail** until it fits. Hard floor of 1 — always keep the most
    # recent turn so the LLM has something to anchor on. The
    # summary text itself is in `prior_summary` so the LLM doesn't
    # lose older context; we just stop carrying raw tail past the
    # budget.
    candidate_tail = list(message_dtos[-keep:])

    def _post_compact_tokens(candidate: list["Message"]) -> int:
        return summary_tokens + estimate_messages_tokens(
            [_dto_to_dict(m) for m in candidate]
        )

    while len(candidate_tail) > 1 and _post_compact_tokens(candidate_tail) > threshold:
        candidate_tail.pop(0)

    if (
        len(candidate_tail) == 1
        and _post_compact_tokens(candidate_tail) > threshold
    ):
        # Even summary + 1 message is over budget. We still keep the
        # 1 — it's the most recent turn and the LLM needs it to know
        # what's being asked. Log so operators can spot runaway
        # conversations.
        logger.warning(
            "compact: post-compact budget still over (conversation=%s "
            "summary_tokens=%d summary_len=%d keep=1 needed=%d threshold=%d)",
            conversation_id,
            summary_tokens,
            len(prior_summary or ""),
            _post_compact_tokens(candidate_tail),
            threshold,
        )

    tail = candidate_tail
    to_archive = message_dtos[: len(message_dtos) - len(tail)]

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
    conversation_id: int | None = None,
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

    system = prompt_book.get(key="agent/compaction") or ""

    if len(to_compress) > _COMPRESS_INPUT_CAP:
        # Try truncating the "[Prior summary]\n..." prefix if it's there
        marker = "[Prior summary]\n"
        if to_compress.startswith(marker):
            tail_start = _COMPRESS_INPUT_CAP - _TRUNCATE_TAIL
            head_end = len(marker) + _TRUNCATE_HEAD  # i.e. first _TRUNCATE_HEAD chars after marker
            truncated = to_compress[:head_end] + "\n[…truncated…]\n" + to_compress[tail_start:]
            if len(truncated) <= _COMPRESS_INPUT_CAP:
                to_compress = truncated
            else:
                return None
        else:
            return None

    from magi.old_bus.firmwares.jobs.callLLMJob import CallLLMJob

    job = CallLLMJob(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": to_compress},
        ],
        contact_id=contact_id,
        max_tokens=1024,
    )
    llm_job_id = bus.llm_job_board.publish(job)
    result = await bus.llm_job_board.wait_for_result(job_id=llm_job_id, timeout=wait_seconds)
    if result is None:
        logger.warning("compact: provider job timed out")
        return None
    if result.status != JobStatus.COMPLETED:
        logger.warning("compact: provider job failed: %s", getattr(result, "error", "?"))
        return None
    resp = getattr(result, "response", None) or {}
    text = (resp.get("text") or "").strip()
    return text or None


__all__ = ["maybe_compact", "call_llm_for_summary"]
