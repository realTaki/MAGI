"""Stable public DTO namespace for the BUS."""

from magi.bus.contracts.agent import (
    A2AInvocationRequest,
    AgentMessage,
    BusClaim,
    BusStoreProtocol,
    DeliveryClaim,
    InboxKind,
    RunResult,
    RunStatus,
)
from magi.bus.contracts.magis import ProviderConfiguration
from magi.bus.contracts.tools import (
    ToolCatalogSnapshot,
    ToolClaim,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

__all__ = [
    "A2AInvocationRequest", "AgentMessage", "BusClaim", "BusStoreProtocol",
    "DeliveryClaim", "InboxKind", "RunResult", "RunStatus", "ProviderConfiguration",
    "ToolCatalogSnapshot", "ToolClaim", "ToolContext", "ToolDefinition", "ToolResult",
]
