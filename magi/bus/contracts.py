"""Small, transport-independent contracts for :mod:`magi.bus`.

These dataclasses deliberately contain plain serialisable values.  A channel
can turn an HTTP, Telegram, task, or future A2A input into an
``AgentMessage`` without importing the agent loop, and an agent worker can
consume it without importing a channel implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

InboxKind = Literal["channel.message.received", "task.triggered", "tool.result", "a2a.result"]
RunStatus = Literal[
    "queued", "running", "waiting_tool", "waiting_a2a", "completed", "failed", "cancelled"
]


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """A request for exactly one agent turn.

    ``event_id`` is supplied by the producer and is the idempotency key.  The
    durable bus creates a ``run_id`` for the turn, which callers can use to
    wait for a compatible synchronous response during the migration away from
    direct ``handle_message`` calls.
    """

    event_id: str
    text: str
    channel: str
    session_id: str | None = None
    uid: int | None = None
    caller_role: str | None = None
    kind: InboxKind = "channel.message.received"
    source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BusClaim:
    """A leased inbox row returned to an agent worker."""

    event_id: str
    run_id: str
    kind: str
    payload: dict[str, Any]
    attempts: int


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
