"""Data contracts for future system-level proactive components.

These types deliberately contain no scheduler, policy, heartbeat or executor.
They let future implementations exchange observable facts and proposed actions
without coupling that decision layer to a particular inbound channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ProactiveSignal:
    """An observed fact that may be relevant to a future policy."""

    kind: str
    source: str
    observed_at: datetime
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """A policy decision awaiting an explicit executor/channel route."""

    kind: str
    rationale: str
    payload: Mapping[str, object] = field(default_factory=dict)
