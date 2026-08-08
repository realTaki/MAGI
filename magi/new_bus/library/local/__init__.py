"""new_bus.library.local — Books for the local SQLite runtime database.

Each module maps to one (or a small group of) SQLite tables.
File names match the Book classes: ``<domain>Book.py``.
"""

from magi.new_bus.library.local.actionItemBook import (
    ALL_PRIORITIES,
    ALL_SOURCES,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    SOURCE_PROACTIVE,
    SOURCE_USER,
    ActionItem,
    ActionItemBook,
)
from magi.new_bus.library.local.agentTurnBook import (
    ACTIVE_PHASES,
    PHASE_CANCELLED,
    PHASE_RUNNING_LLM,
    PHASE_TERMINAL,
    PHASE_WAITING_EFFECTS,
    TURN_LEASE_SECONDS,
    AgentTurn,
    AgentTurnBook,
    AgentTurnStore,
)
from magi.new_bus.library.local.contactBook import (
    ALL_ROLES,
    Contact,
    ContactBook,
    ContactNote,
    ContactNoteBook,
    ROLE_ASSIGNED,
    ROLE_GUEST,
)
from magi.new_bus.library.local.hookSignoffBook import HookSignoff, HookSignoffBook
from magi.new_bus.library.local.mcpServerBook import McpServer, McpServerBook
from magi.new_bus.library.local.memoryBook import (
    ALL_KINDS,
    KIND_FACT,
    KIND_QUICK_NOTE,
    Memory,
    MemoryBook,
)
from magi.new_bus.library.local.sessionBook import (
    Message,
    MessageBook,
    Session,
    SessionBook,
)
from magi.new_bus.library.local.settingBook import Setting, SettingBook
from magi.new_bus.library.local.tasksBook import (
    Channel,
    ChannelEnum,
    Task,
    TaskBook,
    TaskRun,
    TaskRunBook,
)
from magi.new_bus.library.local.tokenUsageBook import TokenUsage, TokenUsageBook
from magi.new_bus.library.local.toolsBook import (
    ToolCatalogState,
    ToolCatalogStateBook,
    ToolCatalogSnapshot,
    ToolDefinition,
    ToolDefinitionBook,
)


__all__ = [
    "ActionItem",
    "ActionItemBook",
    "ACTIVE_PHASES",
    "AgentTurn",
    "AgentTurnBook",
    "AgentTurnStore",
    "ALL_KINDS",
    "ALL_PRIORITIES",
    "ALL_SOURCES",
    "Channel",
    "ChannelEnum",
    "PRIORITY_HIGH",
    "PRIORITY_NORMAL",
    "PHASE_CANCELLED",
    "PHASE_RUNNING_LLM",
    "PHASE_TERMINAL",
    "PHASE_WAITING_EFFECTS",
    "SOURCE_PROACTIVE",
    "SOURCE_USER",
    "TURN_LEASE_SECONDS",
    "ALL_ROLES",
    "Contact",
    "ContactBook",
    "ContactNote",
    "ContactNoteBook",
    "HookSignoff",
    "HookSignoffBook",
    "KIND_FACT",
    "KIND_QUICK_NOTE",
    "McpServer",
    "McpServerBook",
    "Memory",
    "MemoryBook",
    "Message",
    "MessageBook",
    "ROLE_ASSIGNED",
    "ROLE_GUEST",
    "Session",
    "SessionBook",
    "Setting",
    "SettingBook",
    "Task",
    "TaskBook",
    "TaskRun",
    "TaskRunBook",
    "TokenUsage",
    "TokenUsageBook",
    "ToolCatalogState",
    "ToolCatalogStateBook",
    "ToolCatalogSnapshot",
    "ToolDefinition",
    "ToolDefinitionBook",
]