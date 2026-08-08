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
from magi.new_bus.library.local.contactBook import (
    ALL_ROLES,
    Contact,
    ContactBook,
    ContactNote,
    ContactNoteBook,
    ROLE_ASSIGNED,
    ROLE_GUEST,
    SOURCE_EVA,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
)
from magi.new_bus.library.local.hookSignoffBook import HookSignoff, HookSignoffBook
from magi.new_bus.library.local.mcpServerBook import McpServer, McpServerBook
from magi.new_bus.library.local.memoryBook import (
    ALL_KINDS,
    ALL_SOURCES as ALL_MEMORY_SOURCES,
    KIND_IMPORTANT,
    KIND_ONGOING,
    Memory,
    MemoryBook,
    SOURCE_EVA,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
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
    "ALL_KINDS",
    "ALL_PRIORITIES",
    "ALL_SOURCES",
    "Channel",
    "ChannelEnum",
    "PRIORITY_HIGH",
    "PRIORITY_NORMAL",
    "SOURCE_PROACTIVE",
    "SOURCE_USER",
    "ALL_ROLES",
    "Contact",
    "ContactBook",
    "ContactNote",
    "ContactNoteBook",
    "HookSignoff",
    "HookSignoffBook",
    "KIND_IMPORTANT",
    "KIND_ONGOING",
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
    "SOURCE_EVA",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
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