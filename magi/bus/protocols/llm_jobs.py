"""Wire format between callers and :class:`magi.providers.worker.ProvidersWorker`.

A :class:`LLMJob` is published onto the durable queue (one
:class:`magi.bus.db.models.queue.llm_attempt.LLMAttempt` row with
``status="queued"``); the worker claims it, runs the real provider,
and writes back a :class:`LLMJobResult` on the same row's
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
from typing import Any, Literal


# Closed set of caller labels for :attr:`LLMJob.kind` / the
# ``LLMAttempt.phase`` column. The worker does not branch on
# ``kind`` — these are an audit/observability label only — but a
# caller MUST pick one of these three. Adding a fourth requires
# updating both this Literal and the audit-log / dashboards that
# key on it; that's deliberate friction.
LLMJobKind = Literal[
    "chat",          # one agent turn in response to a user message
    "auto_compact",  # chat-history compression
    "auto_title",    # session-title generation
]


@dataclass(frozen=True, slots=True)
class LLMJob:
    """A single LLM invocation request, durable across restarts.

    One job produces exactly one :class:`LLMJobResult` and one
    ``LLMAttempt`` row in the terminal phase. ``attempt_id`` is the
    durable correlation key: callers and the worker both reference it
    in audit / hook emissions / result lookups.
    """

    # --- durable correlation ---
    attempt_id: str
    run_id: str
    inbox_event_id: str | None

    # --- audit-only label (not a protocol-level discriminator) ---
    # Closed set; see :data:`LLMJobKind`. Today the agent turn uses
    # "chat", chat-history compression uses "auto_compact", and
    # session-title generation uses "auto_title". The worker treats
    # ``kind`` as metadata only.
    kind: LLMJobKind

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
class LLMJobResult:
    """What :class:`ProvidersWorker` writes back when the job settles.

    Stored as JSON on the ``LLMAttempt.response`` column for
    successes and ``LLMAttempt.error`` column for failures.

    The shape is the same regardless of ``LLMJob.kind``. The
    caller decides what fields it reads (the agent turn consumes
    ``tool_uses`` and ``assistant_blocks``; ``auto_compact``
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
    "LLMJob",
    "LLMJobKind",
    "LLMJobResult",
]