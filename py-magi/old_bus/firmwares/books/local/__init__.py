"""bus.library.local — Books for the local SQLite runtime database.

Each module maps to one (or a small group of) SQLite tables.
File names match the Book classes: ``<domain>Book.py``.
"""

from old_bus.firmwares.books.local.actionItemBook import (
    ActionItem,
    ActionItemBook,
    ActionPriority,
    ActionSource,
)
from old_bus.firmwares.books.local.contactBook import (
    Contact,
    ContactBook,
    ContactNote,
    ContactNoteBook,
    NoteKind,
    Role,
)
from old_bus.firmwares.books.local.conversationBook import (
    AgentMessageRole,
    Conversation,
    ConversationBook,
    Message,
    MessageBook,
)
from old_bus.firmwares.books.local.hookSignoffBook import (
    HookSignoff,
    HookSignoffBook,
    HookSignoffStatus,
)
from old_bus.firmwares.books.local.mcpServerBook import (
    MCPConnectionType,
    McpServer,
    McpServerBook,
)
from old_bus.firmwares.books.local.memoryBook import (
    Memory,
    MemoryBook,
    MemoryKind,
)
from old_bus.firmwares.books.local.settingBook import (
    CHANNEL_OPTIONS_KEY,
    Setting,
    SettingBook,
)
from old_bus.firmwares.books.local.tasksBook import (
    Task,
    TaskBook,
    TaskRun,
    TaskRunBook,
    TaskRunStatus,
    TaskSource,
)
from old_bus.firmwares.books.local.tokenUsageBook import TokenUsage, TokenUsageBook
from old_bus.firmwares.books.local.toolsBook import (
    ToolCatalogSnapshot,
    ToolCatalogState,
    ToolCatalogStateBook,
    ToolDefinition,
    ToolDefinitionBook,
    ToolSource,
)

__all__ = [
    "ActionItem",
    "ActionItemBook",
    "ActionPriority",
    "ActionSource",
    "CHANNEL_OPTIONS_KEY",
    "Contact",
    "ContactBook",
    "ContactNote",
    "ContactNoteBook",
    "HookSignoff",
    "HookSignoffBook",
    "HookSignoffStatus",
    "AgentMessageRole",
    "MCPConnectionType",
    "McpServer",
    "McpServerBook",
    "Memory",
    "MemoryBook",
    "MemoryKind",
    "Message",
    "MessageBook",
    "NoteKind",
    "Role",
    "Conversation",
    "ConversationBook",
    "Setting",
    "SettingBook",
    "Task",
    "TaskBook",
    "TaskRun",
    "TaskRunBook",
    "TaskRunStatus",
    "TaskSource",
    "TokenUsage",
    "TokenUsageBook",
    "ToolCatalogState",
    "ToolCatalogStateBook",
    "ToolCatalogSnapshot",
    "ToolDefinition",
    "ToolDefinitionBook",
    "ToolSource",
]
