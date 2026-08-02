"""Durable, local message bus for one MAGI runtime.

The bus is intentionally a transport-neutral SQLite middleware.  It owns
durable publication, claiming, leases, retries and recovery; it does *not*
own an agent, channel or tool worker.  Those workers live in their respective
domain packages and use :class:`BusStore` as their coordination boundary.
"""

from magi.bus.contracts import (
    AgentMessage,
    BusClaim,
    DeliveryClaim,
    RunResult,
    ToolClaim,
)
from magi.bus.store import BusStore
from magi.bus.stream import StreamEvent, StreamHub, get_stream_hub

__all__ = [
    "AgentMessage", "BusClaim", "BusStore", "DeliveryClaim", "RunResult", "ToolClaim",
    "StreamEvent", "StreamHub", "get_stream_hub",
]
