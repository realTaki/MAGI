"""new_bus.books.local — Books for the local SQLite runtime database.

Each module maps to one (or a small group of) SQLite tables.
"""

from magi.new_bus.books.local.action_item import ActionItem, ActionItemBook
from magi.new_bus.books.local.contact import (
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
from magi.new_bus.books.local.hook_signoff import HookSignoff, HookSignoffBook
from magi.new_bus.books.local.mcp import McpServer, McpServerBook
from magi.new_bus.books.local.memory import (
    ALL_KINDS,
    KIND_IMPORTANT,
    KIND_ONGOING,
    Memory,
    MemoryBook,
    SOURCE_EVA,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
)
from magi.new_bus.books.local.session import (
    Message,
    MessageBook,
    Session,
    SessionBook,
)
from magi.new_bus.books.local.setting import Setting, SettingBook
from magi.new_bus.books.local.task import (
    Task,
    TaskBook,
    TaskPreset,
    TaskPresetBook,
    TaskRun,
    TaskRunBook,
)
from magi.new_bus.books.local.token_usage import TokenUsage, TokenUsageBook
from magi.new_bus.books.local.tool import (
    ToolCatalogState,
    ToolCatalogStateBook,
    ToolDefinition,
    ToolDefinitionBook,
)


__all__ = [
    "ActionItem",
    "ActionItemBook",
    "ALL_KINDS",
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
    "TaskPreset",
    "TaskPresetBook",
    "TaskRun",
    "TaskRunBook",
    "TokenUsage",
    "TokenUsageBook",
    "ToolCatalogState",
    "ToolCatalogStateBook",
    "ToolDefinition",
    "ToolDefinitionBook",
]
