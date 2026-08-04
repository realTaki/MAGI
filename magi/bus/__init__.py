"""Durable, local message bus for one MAGI runtime.

The bus is the only cross-module boundary: agent / channels / tools
all see it as the application core (read/write protocol + queue).
The :class:`Bus` facade exposes a per-domain service namespace
(``bus.session``, ``bus.memory``, ``bus.tool_jobs``, ...).
"""

from magi.bus.bootstrap import Bus, get_bus, get_bus_store
from magi.bus.contracts import (
    A2AInvocationRequest,
    ActionItemView,
    AgentMessage,
    BusClaim,
    BusStoreProtocol,
    CallerIdentity,
    Channel,
    ChannelEnum,
    ContactView,
    DeliveryClaim,
    DeliveryResult,
    EveRuntimeView,
    InboundMessage,
    MagisAdminView,
    MagisMembershipView,
    MagisRoleView,
    MagisView,
    MagicView,
    MemberRole,
    MembershipBrief,
    MemoryView,
    NoteView,
    OperatorView,
    OutboundDelivery,
    ProviderConfiguration,
    RunResult,
    RuntimeIdentity,
    SearchHit,
    Session,
    SessionMessage,
    SessionSummary,
    ToolCatalogSnapshot,
    ToolClaim,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from magi.bus.contracts.lifecycle import (
    KubernetesBackendDetail,
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)
from magi.bus.contracts.runtime import BackendKind, RuntimeEndpoint
from magi.bus.services.runtime import (
    BackendDispatcherService,
    OrchestratorUnavailable,
    RuntimeRegistryService,
)
from magi.bus.store import BusStore
from magi.bus.stream import StreamEvent, StreamHub, get_stream_hub


__all__ = [
    # public domain facade
    "Bus",
    "bootstrap",
    "get_bus",
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
    # channel-side DTOs
    "Channel", "ChannelEnum", "DeliveryResult", "InboundMessage", "OutboundDelivery",
    # session DTOs
    "Session", "SessionMessage", "SessionSummary", "SearchHit",
    # contact DTOs
    "ContactView", "NoteView",
    # memory DTOs
    "MemoryView",
    # magis DTOs
    "MagisView", "MagisAdminView", "MagisRoleView", "MagisMembershipView",
    "MagicView", "MembershipBrief", "EveRuntimeView", "OperatorView",
    "MemberRole", "RuntimeIdentity", "ProviderConfiguration",
    # auth DTOs
    "CallerIdentity",
    # action_item DTOs
    "ActionItemView",
    # Phase 2 — platform-neutral Runtime lifecycle + endpoint DTOs.
    "BackendKind",
    "RuntimeEndpoint",
    "RuntimeSpec",
    "RuntimeOperationResult",
    "MagisProvisionResult",
    "KubernetesBackendDetail",
    # Phase 2 — Runtime lifecycle + registry services.
    "BackendDispatcherService",
    "RuntimeRegistryService",
    "OrchestratorUnavailable",
]

