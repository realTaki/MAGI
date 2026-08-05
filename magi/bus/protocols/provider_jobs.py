"""Wire format between callers and :class:`magi.providers.worker.ProvidersWorker`.

A :class:`ProviderJob` is published onto the durable queue (one
:class:`magi.bus.models.queue.llm_attempt.LLMAttempt` row with
``status="queued"``); the worker claims it, runs the real provider,
and writes back a :class:`ProviderJobResult` on the same row's
``response`` (success) or ``error`` (failure) JSON column. The
result also surfaces to the caller via the ``provider.completed``
``AgentInbox`` event — see :data:`magi.bus.protocols.agent.InboxKind`.

Design principle
================

The provider worker is **a dumb LLM invoker**. Whether the caller is
the agent turn (``agent.step``), chat-history compression
(``compaction.summary``), or session-title generation
(``auto_title``) is the caller's concern, not the provider's. The
job carries ``kind`` for **audit / observability only** (the
``LLMAttempt.phase`` column records it; the audit-log plugin can
filter by it); the worker does not branch on ``kind``. Caller-side
post-processing of the result (e.g. ``auto_title`` writing the
title via :func:`magi.bus.session.set_title_if_null`) is done by
the caller after it observes the ``provider.completed`` event or
polls the result row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderJob:
    """A single LLM invocation request, durable across restarts.

    One job produces exactly one :class:`ProviderJobResult` and one
    ``LLMAttempt`` row in the terminal phase. ``attempt_id`` is the
    durable correlation key: callers and the worker both reference it
    in audit / hook emissions / result lookups.
    """

    # --- durable correlation ---
    attempt_id: str
    run_id: str
    inbox_event_id: str | None

    # --- audit-only label (not a protocol-level discriminator) ---
    # Free-form string so adding a new caller (e.g. "summary.replay",
    # "prompt.eval") doesn't require a protocol change. Today the
    # agent turn uses "agent.step"; the worker treats this as
    # metadata only.
    kind: str

    # --- request payload the worker hands to the provider ---
    system: str | None
    messages: tuple[dict[str, Any], ...]
    max_tokens: int = 1024
    tools: tuple[dict[str, Any], ...] | None = None
    streaming: bool = False

    # --- caller-specific opaque payload ---
    # The worker does not interpret this. The originating caller
    # may store anything it needs to post-process the result; e.g.
    # auto_title puts ``{"uid": int, "session_id": str}`` here.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderJobResult:
    """What :class:`ProvidersWorker` writes back when the job settles.

    Stored as JSON on the ``LLMAttempt.response`` column for
    successes and ``LLMAttempt.error`` column for failures.

    The shape is the same regardless of ``ProviderJob.kind``. The
    caller decides what fields it reads (the agent turn consumes
    ``tool_uses`` and ``assistant_blocks``; ``compaction.summary``
    and ``auto_title`` only read ``text``).
    """

    attempt_id: str
    status: str  # "completed" | "failed"
    text: str = ""
    thinking: str | None = None
    tool_uses: tuple[dict[str, Any], ...] = ()
    assistant_blocks: tuple[dict[str, Any], ...] = ()
    provider: str = ""
    model: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    error_detail: str | None = None
    # A stable short code; for ``LLMError`` subclasses this is the
    # subclass name (e.g. ``"LLMAuthError"``). For LLM-side status
    # checks it's ``"magi.llm_credentials_required"``,
    # ``"magi.run_deadline_exceeded"``,
    # ``"chat.provider_crashed"``, etc. Empty string on success.
    error_code: str = ""


__all__ = [
    "ProviderJob",
    "ProviderJobResult",
]
