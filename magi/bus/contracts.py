"""Small, transport-independent contracts for :mod:`magi.bus`.

These dataclasses deliberately contain plain serialisable values.  A channel
can turn an HTTP, Telegram, task, or future A2A input into an
``AgentMessage`` without importing the agent loop, and an agent worker can
consume it without importing a channel implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

InboxKind = Literal[
    "channel.message.received",
    "task.triggered",
    "tool.result",
    "tool.failed",
    "run.steer",
    "run.cancel",
    "a2a.request",
    "a2a.result",
]
RunStatus = Literal[
    "queued", "running", "waiting_tool", "waiting_a2a", "completed", "failed", "cancelled"
]


@dataclass(frozen=True, slots=True)
class A2AInvocationRequest:
    """An actor-owned peer message effect.

    ``expect_reply`` is deliberately explicit: ordinary MAGI communication is
    one-way; only a caller that asks for a response parks its run.
    """

    tool_call_id: str
    target_magic_id: int
    text: str
    expect_reply: bool = False
    timeout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """A request for exactly one agent turn.

    ``event_id`` is supplied by the producer and is the idempotency key.  The
    durable bus creates a ``run_id`` for the turn, which callers can use to
    wait for a compatible synchronous response during the migration away from
    direct synchronous agent calls.
    """

    event_id: str
    text: str
    channel: str
    session_id: str | None = None
    uid: int | None = None
    caller_role: str | None = None
    kind: InboxKind = "channel.message.received"
    source_id: str | None = None
    # ``conversation_id`` is deliberately separate from a producer event.
    # A session is the normal conversation identity for WebUI/TG; producers
    # without sessions may supply their own stable identity.  Keeping it in
    # the envelope lets the store attach a later human message to a waiting
    # run without importing a channel implementation.
    conversation_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    target_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BusClaim:
    """A leased inbox row returned to an agent worker."""

    event_id: str
    run_id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolClaim:
    """A leased tool job returned to a tools-owned worker."""

    job_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """A leased committed reply awaiting channel delivery."""

    delivery_id: str
    run_id: str | None
    channel: str
    destination: str | None
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True, slots=True)
class RunResult:
    """The terminal or in-progress state of an agent run."""

    run_id: str
    status: str
    reply: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


class BusStoreProtocol(Protocol):
    """Runtime-facing store boundary; SQLite is an implementation detail."""

    def publish_agent_message(self, message: AgentMessage) -> str: ...
    def claim_next_agent_message(self, worker_id: str, *, lease_seconds: int = 60) -> BusClaim | None: ...
    def recover_expired_leases(self) -> int: ...
    def expire_a2a_invocations(self) -> int: ...
    def get_run_result(self, run_id: str) -> RunResult | None: ...
    def cancel_run(self, run_id: str, *, reason: str = "cancelled_by_user") -> bool: ...
    def complete_agent_input(self, event_id: str) -> None: ...
    def pending_steering_inputs(self, run_id: str) -> list[dict[str, Any]]: ...
    def load_tool_continuation(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None: ...
    def start_llm_attempt(self, run_id: str, inbox_event_id: str) -> str: ...
    def fail_llm_attempt(self, attempt_id: str, error: str) -> None: ...
    def complete_agent_message(
        self,
        event_id: str,
        reply: str,
        *,
        delivery_destination: str | None = None,
        continuation: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        attempt_result: dict[str, Any] | None = None,
    ) -> None: ...
    def commit_agent_transition(
        self,
        event_id: str,
        *,
        reply: str | None = None,
        delivery_destination: str | None = None,
        continuation: dict[str, Any] | None = None,
        jobs: list[dict[str, Any]] | None = None,
        a2a_requests: list[A2AInvocationRequest] | None = None,
        attempt_id: str | None = None,
        attempt_result: dict[str, Any] | None = None,
    ) -> None: ...
    def wait_for_tools(
        self,
        event_id: str,
        *,
        continuation: dict[str, Any],
        jobs: list[dict[str, Any]],
        a2a_requests: list[A2AInvocationRequest] | None = None,
        attempt_id: str | None = None,
        attempt_result: dict[str, Any] | None = None,
    ) -> None: ...
    def fail_agent_message(self, event_id: str, *, error_code: str, error_detail: str) -> None: ...
