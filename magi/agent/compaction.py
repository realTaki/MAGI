"""Auto-compaction for long chat sessions (D.17).

Extracted from :mod:`magi.agent.loop` for the same
size-budget reason as :mod:`magi.agent.token_usage`:
the loop module is otherwise dominated by prompt
building / compaction / token accounting that don't
read or write any state the loop keeps in locals.

Three surfaces pinned:

  - :func:`maybe_compact` — entry called from the agent
    loop on every chat turn. Estimates the in-memory
    ``messages`` token cost; if over the configured
    threshold, calls the LLM for a summary and rewrites
    the on-disk session (archive the older entries,
    prepend the summary, keep the last K verbatim).
  - :func:`call_llm_for_summary` — the compression
    LLM call. ``None`` on any failure so the caller can
    fall through ("no compaction happened this turn")
    rather than blocking the chat.
  - :func:`chat_to_session_message` and
    :func:`_employee_id_for_log` — small helpers; the
    first converts the runtime message shape to the
    persisted ``SessionMessage`` shape, the second is a
    no-op kept so the failure-path log line in
    :func:`call_llm_for_summary` doesn't ``NameError``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.agent.llm import ChatMessage, get_provider
from magi.agent.llm.tokens import estimate_messages_tokens
from magi.agent.memory.session import (
    SessionMessage,
    SessionStore,
    new_session_id,
    utcnow_iso,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("magi.agent.compaction")


async def maybe_compact(
    state_dir: str,
    uid: int,
    session_id: str | None,
    messages: list["ChatMessage"],
    *,
    employee_provider: str,
    employee_api_key: str,
    employee_model: str | None,
) -> None:
    """Estimate token cost of ``messages``. If over the
    configured threshold, run one compaction pass: move
    the older entries into ``sess.archive``, prepend a
    summary at ``messages[0]``, and shrink ``messages``
    in-place so the next LLM call sees
    ``[summary, ...recent K]``.

    No-op when there's no session yet (first turn of a
    brand-new conversation; nothing to compact).
    """
    if not session_id:
        return

    # Lazy import to avoid pulling settings.py at agent
    # module load (the SQLAlchemy dependency inside
    # settings.py would otherwise leak into tests that
    # only want handle_message).
    from magi.channels.webui.api.system_settings import (
        get_compact_context_window,
        get_compact_threshold_pct,
        get_compact_keep_recent,
    )

    keep = get_compact_keep_recent(state_dir)
    # Already short enough: nothing to compact.
    if len(messages) <= keep:
        return

    total = estimate_messages_tokens(messages)
    threshold = (
        get_compact_context_window(state_dir)
        * get_compact_threshold_pct(state_dir)
        // 100
    )
    if total <= threshold:
        return

    # Slice: oldest entries (everything before the last K)
    # are about to be archived; the last K stays verbatim.
    to_archive = messages[:-keep]

    # Call LLM for summary. Failure → no compaction this
    # turn; the next turn will try again (the in-memory
    # ``messages`` list is unchanged so the operator sees
    # the full context this turn at least).
    summary_text = await call_llm_for_summary(
        state_dir=state_dir,
        employee_provider=employee_provider,
        employee_api_key=employee_api_key,
        employee_model=employee_model,
        to_compress=to_archive,
    )
    if not summary_text:
        logger.warning(
            "compact: no summary produced; session will retry "
            "next turn (messages=%d, total_tokens~%d)",
            len(messages), total,
        )
        return

    # Build summary system message. Lives at messages[0]
    # going forward.
    summary_msg = ChatMessage(
        role="user",  # later mapped to "user" via _build_messages_from_session
        content=f"[Prior conversation summary]\n{summary_text}",
    )

    # Persist: append old messages to archive, prepend
    # summary to active, update active_tail_count and
    # last_compaction_at. Atomic write via _write().
    store = SessionStore(state_dir)
    sess = store.get(uid, session_id)
    if sess is None:
        return  # session disappeared mid-call; skip
    sess.archive.extend(chat_to_session_message(m) for m in to_archive)
    sess.last_compaction_at = utcnow_iso()
    sess.active_tail_count = keep
    sess.messages = [chat_to_session_message(summary_msg)] + [
        chat_to_session_message(m) for m in messages[-keep:]
    ]
    try:
        store._write(sess, bump_updated=False)
    except Exception:
        logger.exception(
            "compact: persist failed (session=%s); in-memory "
            "messages already shrunk, on-disk archive NOT "
            "written. Next chat will re-compact.", session_id,
        )

    # Shrink in-memory list. The caller (agent loop)
    # passes its own ``messages`` list in; we mutate it
    # in-place so the next LLM call sees the compacted
    # view.
    summary_msg_for_llm = ChatMessage(
        role="user",  # ChatMessage Literal doesn't allow "system" here
        content=f"[Prior conversation summary]\n{summary_text}",
    )
    messages[:] = [summary_msg_for_llm] + messages[-keep:]


async def call_llm_for_summary(
    *,
    state_dir: str,
    employee_provider: str,
    employee_api_key: str,
    employee_model: str | None,
    to_compress: list["ChatMessage"],
) -> str | None:
    """One LLM call to compress ``to_compress`` into a
    structured summary. Uses the same provider + creds
    as the main chat (the employee is paying for it).
    Returns the summary text, or ``None`` on any failure
    so the caller can fall back to "no compaction
    happened".
    """
    from magi.agent.prompts import load_compaction_prompt

    system = load_compaction_prompt()
    # Serialise the messages as plain text. Format mirrors
    # the standard Anthropic Messages API ``role: text``
    # lines so the LLM sees familiar structure.
    user_lines: list[str] = []
    for m in to_compress:
        who = m.role.upper()
        user_lines.append(f"[{who}]\n{m.content}")
    user_content = "\n\n".join(user_lines)
    if len(user_content) > 6000:
        # Hard cap: if the input to the compaction call
        # itself exceeds ~24 K tokens (the heuristic gives
        # 24 K at 4 chars/token), the call is too expensive
        # to make sense for v0. Skip and log.
        return None
    try:
        provider = get_provider(employee_provider, employee_api_key, employee_model)
        result = await provider.chat(
            system=system,
            messages=[ChatMessage(role="user", content=user_content)],
            max_tokens=1024,
        )
        text = (result.text or "").strip()
        return text or None
    except Exception:
        logger.exception(
            "compact: LLM call failed (employee=%s); skipping",
            _employee_id_for_log(state_dir),
        )
        return None


def chat_to_session_message(m: "ChatMessage") -> SessionMessage:
    """Translate a runtime ChatMessage to the storage
    SessionMessage shape. v0 archive doesn't carry tool
    blocks (those are in-loop only); ``text`` is enough.
    The timestamp is "now" (we're rewriting history, the
    original timestamps would be misleading).
    """
    role = m.role if m.role in ("user", "assistant", "system") else "user"
    return SessionMessage(
        role=role,
        text=m.content,
        ts=utcnow_iso(),
        message_id=new_session_id(),
    )


def _employee_id_for_log(state_dir: str) -> int | None:
    """Best-effort uid for log lines from the
    compact helper. Reads the cookie-side admin gate is
    not available here (we're inside the agent loop, not
    the API layer), so this just returns None; the
    caller has access to uid directly and prefers
    passing it. We keep the symbol so the failure-path
    log line in :func:`call_llm_for_summary` doesn't
    ``NameError``.
    """
    return None


__all__ = [
    "maybe_compact",
    "call_llm_for_summary",
    "chat_to_session_message",
]
