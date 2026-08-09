"""Auto-compaction for long chat sessions — new_bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.agent.tokens import estimate_messages_tokens

if TYPE_CHECKING:
    from magi.providers import ChatMessage
    from magi.new_bus import NewBus

logger = logging.getLogger("magi.agent.compaction")

_DEFAULT_COMPACTION_KEEP = 8
_DEFAULT_CONTEXT_WINDOW = 200_000
_DEFAULT_THRESHOLD_PCT = 80


async def maybe_compact(
    uid: int,
    session_id: str | None,
    messages: list["ChatMessage"],
    *,
    bus: "NewBus",
) -> None:
    """Estimate token cost. If over threshold, run one compaction pass."""
    if not session_id:
        return

    try:
        keep_raw = bus.settings_book.get(key="compaction.keep_tail")
        keep = int(keep_raw) if keep_raw else _DEFAULT_COMPACTION_KEEP
        window_raw = bus.settings_book.get(key="compaction.context_window")
        context_window = int(window_raw) if window_raw else _DEFAULT_CONTEXT_WINDOW
        pct_raw = bus.settings_book.get(key="compaction.threshold_pct")
        threshold_pct = int(pct_raw) if pct_raw else _DEFAULT_THRESHOLD_PCT
    except Exception:
        context_window, threshold_pct, keep = (
            _DEFAULT_CONTEXT_WINDOW, _DEFAULT_THRESHOLD_PCT, _DEFAULT_COMPACTION_KEEP,
        )

    if len(messages) <= keep:
        return

    total = estimate_messages_tokens(messages)
    threshold = context_window * threshold_pct // 100
    if total <= threshold:
        return

    to_archive = messages[:-keep]

    summary_text = await call_llm_for_summary(to_compress=to_archive, bus=bus)
    if not summary_text:
        logger.warning("compact: no summary (messages=%d, tokens~%d)", len(messages), total)
        return

    try:
        sess = bus.sessions_book.get_for_owner(uid=uid, session_id=session_id)
        if sess is None:
            return
        messages[:] = [
            type(messages[0])(role="user", content=f"[Prior conversation summary]\n{summary_text}")
        ] + messages[-keep:]
    except Exception:
        logger.exception("compact persist failed (session=%s)", session_id)


async def call_llm_for_summary(
    *,
    to_compress: list["ChatMessage"],
    wait_seconds: float = 30.0,
    bus: "NewBus",
) -> str | None:
    """One LLM call to compress *to_compress* into a summary."""
    from magi.prompts import load_compaction_prompt

    system = load_compaction_prompt()
    user_lines: list[str] = []
    for m in to_compress:
        who = m.role.upper()
        user_lines.append(f"[{who}]\n{m.content}")
    user_content = "\n\n".join(user_lines)
    if len(user_content) > 6000:
        return None

    from magi.new_bus.guild.callLLMJob import CallLLMJob

    job = CallLLMJob(
        messages=(
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ),
        max_tokens=1024,
        parameters={"phase": "auto_compact"},
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
