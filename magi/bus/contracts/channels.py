"""Channel-side DTOs exchanged with the bus.

These describe the boundary between a channel adapter (TG, WebUI, task,
A2A) and the bus.  Channels publish ``InboundMessage`` rows; the bus
emits ``OutboundDelivery`` rows for the channel to consume; the channel
writes ``DeliveryResult`` back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A channel-side request to enqueue an agent turn.

    Channels convert their native request shape (TG update, WebUI
    POST, task trigger, A2A request) into this DTO and call
    ``bus.agent_runs.publish_input``.
    """

    event_id: str
    text: str
    channel: str
    session_id: Optional[str] = None
    uid: Optional[int] = None
    caller_role: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    external_event_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    deadline_at: Optional[str] = None
    target_run_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboundDelivery:
    """A committed channel delivery effect awaiting external I/O.

    Returned by the bus (via :class:`magi.bus.contracts.DeliveryClaim`)
    to channel delivery workers for actual transmission.
    """

    delivery_id: str
    channel: str
    destination: Optional[str]
    payload: dict[str, Any]
    run_id: Optional[str] = None
    event_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Channel-side completion record written back to the bus."""

    delivery_id: str
    success: bool
    error_detail: Optional[str] = None
    next_delay_seconds: Optional[int] = None


# Re-export the channel enum so callers (tools, agent) can name a
# channel without importing from ``magi.channels`` directly. The
# enum itself is a stable string-typed contract; only the channel
# adapters need to interpret each value.
from magi.channels import Channel as ChannelEnum  # noqa: E402,F401
