"""Bus services: domain-partitioned facades over the bus.

The services are the only public bus surface for callers outside
``magi.bus``. Each service wraps either a :class:`magi.bus.store.BusStore`
queue method (durable) or direct SQLAlchemy access (per-domain CRUD
backed by ``magi.bus.db.models``).
"""

from __future__ import annotations

from magi.bus.jobs.services.action_item import ActionItemService
from magi.bus.jobs.protocols.action_item import ActionItemView
from magi.bus.jobs.services.agent_runs import AgentRunsService
from magi.bus.jobs.services.auth import AuthService
from magi.bus.jobs.services.contact import ContactsService
from magi.bus.jobs.services.connector import ConnectorService
from magi.bus.jobs.services.delivery import DeliveryService
from magi.bus.jobs.services.dispatcher import DispatcherService
from magi.bus.jobs.services.magic import MagicService
from magi.bus.jobs.services.magis import MagisService
from magi.bus.jobs.services.memory import MemoryService
from magi.bus.jobs.services.mcp import McpService
from magi.bus.jobs.services.session import SessionService
from magi.bus.jobs.services.setting import SettingsService
from magi.bus.jobs.services.task import TaskService
from magi.bus.jobs.services.task_scheduler_bridge import TaskSchedulerBridge
from magi.bus.jobs.services.token_usage import TokenUsageService
from magi.bus.jobs.services.tool_catalog import (
    CatalogRevisionConflict,
    ToolCatalogService,
    ToolCatalogSnapshot,
    ToolCatalogValidationError,
    ToolDefinition,
)
from magi.bus.jobs.services.tool_jobs import ToolJobsService


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
    "TaskSchedulerBridge",
    "TokenUsageService",
    "ToolCatalogService",
    "ToolCatalogSnapshot",
    "ToolCatalogValidationError",
    "ToolDefinition",
    "ToolJobsService",
]

