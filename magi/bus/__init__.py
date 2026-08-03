"""Durable, local message bus for one MAGI runtime.

The bus is the only cross-module boundary: agent / channels / tools
all see it as the application core (read/write protocol + queue).
The :class:`Bus` facade exposes a per-domain service namespace
(``bus.session``, ``bus.memory``, ``bus.tool_jobs``, ...).
"""

from magi.bus.bootstrap import Bus, bootstrap
from magi.bus.contracts.agent import (
    AgentMessage,
    A2AInvocationRequest,
    BusClaim,
    BusStoreProtocol,
    DeliveryClaim,
    RunResult,
)
from magi.bus.contracts.tools import (
    ToolClaim,
    ToolCatalogSnapshot,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from magi.bus.store import BusStore
from magi.bus.stream import StreamEvent, StreamHub, get_stream_hub


__all__ = [
    # public domain facade
    "Bus",
    "bootstrap",
    # queue + transport
    "BusStore",
    "BusStoreProtocol",
    "StreamEvent",
    "StreamHub",
    "get_stream_hub",
    # agent-side DTOs
    "AgentMessage",
    "A2AInvocationRequest",
    "BusClaim",
    "DeliveryClaim",
    "RunResult",
    # tool-side DTOs
    "ToolClaim",
    "ToolCatalogSnapshot",
    "ToolContext",
    "ToolDefinition",
    "ToolResult",
]
