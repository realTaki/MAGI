"""Stable public DTO namespace for the BUS."""

from magi.bus.protocols.agent import (
    A2AInvocationRequest,
    AgentMessage,
    BusClaim,
    BusStoreProtocol,
    DeliveryClaim,
    InboxKind,
    RunResult,
    RunStatus,
)
from magi.bus.protocols.channels import (
    Channel,
    ChannelEnum,
    DeliveryResult,
    InboundMessage,
    OutboundDelivery,
)
from magi.bus.protocols.magis import (
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
from magi.bus.protocols.session import (
    SearchHit,
    Session,
    SessionMessage,
    SessionSummary,
)
from magi.bus.protocols.contact import ContactView, NoteView
from magi.bus.protocols.memory import MemoryView
from magi.bus.protocols.tools import (
    ToolCatalogSnapshot,
    ToolClaim,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from magi.bus.protocols.auth import CallerIdentity
from magi.bus.protocols.action_item import ActionItemView
from magi.bus.protocols.control_jobs import (
    PROVIDER_CONFIG_CHANGED,
    ControlJobKind,
)
from magi.bus.protocols.llm_jobs import LLMJob, LLMJobKind, LLMJobResult


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
    "LLMJob",
    "LLMJobKind",
    "LLMJobResult",
    # transient control jobs (provider.config_changed)
    "ControlJobKind",
    "PROVIDER_CONFIG_CHANGED",
]  # closes __all__
