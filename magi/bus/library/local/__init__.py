"""bus.library.local — Books for the local SQLite runtime database.

Each module maps to one (or a small group of) SQLite tables.
File names match the Book classes: ``<domain>Book.py``.
"""

from magi.bus.library.local.actionItemBook import (
    ALL_PRIORITIES,
    ALL_SOURCES,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    SOURCE_PROACTIVE,
    SOURCE_USER,
    ActionItem,
    ActionItemBook,
)
from magi.bus.library.local.contactBook import (
    ALL_ROLES,
    Contact,
    ContactBook,
    ContactNote,
    ContactNoteBook,
    ROLE_ASSIGNED,
    ROLE_GUEST,
)
from magi.bus.library.local.hookSignoffBook import HookSignoff, HookSignoffBook
from magi.bus.library.local.mcpServerBook import McpServer, McpServerBook
from magi.bus.library.local.memoryBook import (
    ALL_KINDS,
    KIND_FACT,
    KIND_QUICK_NOTE,
    Memory,
    MemoryBook,
)
from magi.bus.library.local.sessionBook import (
    Message,
    MessageBook,
    Session,
    SessionBook,
)
from magi.bus.library.local.settingBook import MCPTimeout, Setting, SettingBook
from magi.bus.library.local.tasksBook import (
    Channel,
    ChannelEnum,
    Task,
    TaskBook,
    TaskRun,
    TaskRunBook,
)
from magi.bus.library.local.tokenUsageBook import TokenUsage, TokenUsageBook
from magi.bus.library.local.toolsBook import (
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
    "MCPTimeout",
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