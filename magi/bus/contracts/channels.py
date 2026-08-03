"""Channel-side DTOs exchanged with the bus.

These describe the boundary between a channel adapter (TG, WebUI, task,
A2A) and the bus.  Channels publish ``InboundMessage`` rows; the bus
emits ``OutboundDelivery`` rows for the channel to consume; the channel
writes ``DeliveryResult`` back.

The ``Channel`` enum lives here (not in :mod:`magi.channels`) because
the bus owns the cross-package vocabulary.  Channel adapters import it
from the bus; tools, agent, and channel code can name a channel without
having to depend on the channel package itself.  ``magi.channels``
re-exports it for back-compat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional


class Channel(StrEnum):
    """Typed channel identifiers used across bus payloads,
    session store, and dispatcher.

    Replaces free-form ``channel: str`` with a compile-time-
    checkable enum. A typo like ``"telegram"`` instead of
    ``"tg"`` is now caught by the type checker.

    The bus owns this vocabulary: tools, agent, and channel
    adapters all import it from :mod:`magi.bus.contracts`.
    The legacy ``magi.channels.Channel`` re-export is kept
    so adapter code that says ``from magi.channels import
    Channel`` keeps working.
    """

    TG = "tg"
    """Telegram bot channel (EVA → user)."""

    WEBUI = "webui"
    """WebUI chat console (ADAM → operator)."""

    A2A = "a2a"
    """Agent-to-Agent channel — MAGI peers exchange messages via
    HMAC-signed HTTP, scoped to peers in the same MAGIS by
    default. See ``magi.channels.a2a`` for the design."""

    SCHEDULED = "scheduled"
    """Internal scheduled-task channel — fires persisted tasks on a schedule."""


# Back-compat alias.  ``ChannelEnum`` is the historical name used
# in some modules; keep it pointing at the same class.
ChannelEnum = Channel


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

