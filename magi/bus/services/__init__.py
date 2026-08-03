"""Bus services: domain-partitioned facades over the bus.

The services are the only public bus surface for callers outside
``magi.bus``. Each service wraps either a :class:`magi.bus.store.BusStore`
queue method (durable) or direct SQLAlchemy access (per-domain CRUD
backed by ``magi.bus.models``).
"""

from __future__ import annotations

from magi.bus.services.action_item import ActionItemService
from magi.bus.contracts.action_item import ActionItemView
from magi.bus.services.agent_runs import AgentRunsService
from magi.bus.services.auth import AuthService
from magi.bus.services.contact import ContactsService
from magi.bus.services.connector import ConnectorService
from magi.bus.services.delivery import DeliveryService
from magi.bus.services.dispatcher import DispatcherService
from magi.bus.services.magic import MagicService
from magi.bus.services.magis import MagisService
from magi.bus.services.memory import MemoryService
from magi.bus.services.mcp import McpService
from magi.bus.services.session import SessionService
from magi.bus.services.setting import SettingsService
from magi.bus.services.task import TaskService
from magi.bus.services.token_usage import TokenUsageService
from magi.bus.services.tool_catalog import (
    CatalogRevisionConflict,
    ToolCatalogService,
    ToolCatalogSnapshot,
    ToolCatalogValidationError,
    ToolDefinition,
)
from magi.bus.services.tool_jobs import ToolJobsService


__all__ = [
    "ActionItemService",
    "ActionItemView",
    "AgentRunsService",
    "AuthService",
    "CatalogRevisionConflict",
    "ContactsService",
    "ConnectorService",
    "DeliveryService",
    "DispatcherService",
    "MagicService",
    "MagisService",
    "MemoryService",
    "McpService",
    "SessionService",
    "SettingsService",
    "TaskService",
    "TokenUsageService",
    "ToolCatalogService",
    "ToolCatalogSnapshot",
    "ToolCatalogValidationError",
    "ToolDefinition",
    "ToolJobsService",
]

