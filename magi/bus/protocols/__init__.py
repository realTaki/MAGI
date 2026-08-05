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
from magi.bus.contracts.channels import (
    Channel,
    ChannelEnum,
    DeliveryResult,
    InboundMessage,
    OutboundDelivery,
)
from magi.bus.contracts.magis import (
    EvaRuntimeView,
    MagisAdminView,
    MagisMembershipView,
    MagisRoleView,
    MagisView,
    MagicView,
    MemberRole,
    MembershipBrief,
    OperatorView,
    ProviderConfiguration,
    RuntimeIdentity,
)
from magi.bus.contracts.session import (
    SearchHit,
    Session,
    SessionMessage,
    SessionSummary,
)
from magi.bus.contracts.contact import ContactView, NoteView
from magi.bus.contracts.memory import MemoryView
from magi.bus.contracts.tools import (
    ToolCatalogSnapshot,
    ToolClaim,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from magi.bus.contracts.auth import CallerIdentity
from magi.bus.contracts.action_item import ActionItemView
from magi.bus.contracts.provider_jobs import ProviderJob, ProviderJobResult


__all__ = [
    # agent
    "A2AInvocationRequest", "AgentMessage", "BusClaim", "BusStoreProtocol",
    "DeliveryClaim", "InboxKind", "RunResult", "RunStatus",
    # channels
    "Channel", "ChannelEnum", "DeliveryResult", "InboundMessage", "OutboundDelivery",
    # magis
    "EvaRuntimeView", "MagisAdminView", "MagisMembershipView", "MagisRoleView",
    "MagisView", "MagicView", "MemberRole", "MembershipBrief",
    "OperatorView", "ProviderConfiguration", "RuntimeIdentity",
    # session
    "SearchHit", "Session", "SessionMessage", "SessionSummary",
    # contact
    "ContactView", "NoteView",
    # memory
    "MemoryView",
    # tools
    "ToolCatalogSnapshot", "ToolClaim", "ToolContext", "ToolDefinition", "ToolResult",
    # auth
    "CallerIdentity",
    # action_item
    "ActionItemView",
    # provider jobs (PR 2 — Phase B)
    "ProviderJob",
    "ProviderJobResult",
]  # closes __all__
