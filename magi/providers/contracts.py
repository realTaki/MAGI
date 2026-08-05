"""Wire format between callers and :class:`magi.providers.worker.ProvidersWorker`.

A :class:`ProviderJob` is published onto the durable
queue (one :class:`magi.bus.models.queue.llm_attempt.LLMAttempt`
row with ``status="queued"``); the worker claims it, runs the
real provider, and writes back a :class:`ProviderJobResult` on
the same row's ``response`` (success) or ``error`` (failure)
JSON column. The result also surfaces to the caller via the
``provider.completed`` ``AgentInbox`` event — see
:data:`magi.bus.contracts.agent.InboxKind`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ProviderJobKind = Literal[
    "agent.step",
    "compaction.summary",
    "auto_title",
]


@dataclass(frozen=True, slots=True)
class ProviderJob:
    """A single LLM invocation request, durable across restarts.

    One job produces exactly one :class:`ProviderJobResult` and one
    ``LLMAttempt`` row in the terminal phase. ``attempt_id`` is the
    durable correlation key: callers and the worker both reference it
    in audit / hook emissions / result lookups.

    ``kind`` decides which branch of the request payload the worker
    cares about (e.g. ``"auto_title"`` only needs the first user
    message and an extra ``{uid, session_id}`` to write the title
    back). The same dataclass is reused for all three v1 call sites
    so the queue protocol stays uniform.
    """

    attempt_id: str
    run_id: str
    inbox_event_id: str | None
    kind: ProviderJobKind
    system: str | None
    messages: tuple[dict[str, Any], ...]
    max_tokens: int = 1024
    tools: tuple[dict[str, Any], ...] | None = None
    streaming: bool = False
    # Per-kind payload:
    #   - "agent.step"         -> nothing extra
    #   - "compaction.summary" -> {"summary_max_chars": int}
    #   - "auto_title"         -> {"uid": int, "session_id": str}
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderJobResult:
    """What :class:`ProvidersWorker` writes back when the job settles.

    Stored as JSON on the ``LLMAttempt.response`` column for
    successes and ``LLMAttempt.error`` column for failures. The
    ``agent.step`` consumer rebuilds an ``AgentStepResult`` from
    this; ``compaction.summary`` and ``auto_title`` only read
    ``text`` (or ``error_detail`` on the failure path).
    """

    attempt_id: str
    status: Literal["completed", "failed"]
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
    # subclass name (e.g. ``"LLMAuthError"``), for LLM-side status
    # checks it's ``"magi.llm_credentials_required"``,
    # ``"magi.run_deadline_exceeded"``,
    # ``"chat.provider_crashed"``, etc. Empty string on success.
    error_code: str = ""


__all__ = [
    "ProviderJobKind",
    "ProviderJob",
    "ProviderJobResult",
]
