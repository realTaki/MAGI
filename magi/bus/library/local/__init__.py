"""bus.library.local — Books for the local SQLite runtime database.

Each module maps to one (or a small group of) SQLite tables.
File names match the Book classes: ``<domain>Book.py``.
"""

from magi.bus.library.local.actionItemBook import (
    ActionItem,
    ActionItemBook,
    ActionPriority,
    ActionSource,
)
from magi.bus.library.local.contactBook import (
    Contact,
    ContactBook,
    ContactNote,
    ContactNoteBook,
    NoteKind,
    Role,
)
from magi.bus.library.local.conversationBook import (
    Conversation,
    ConversationBook,
    Message,
    MessageBook,
)
from magi.bus.library.local.hookSignoffBook import HookSignoff, HookSignoffBook
from magi.bus.library.local.mcpServerBook import McpServer, McpServerBook
from magi.bus.library.local.memoryBook import (
    Memory,
    MemoryBook,
    MemoryKind,
)
from magi.bus.library.local.settingBook import MCPTimeout, Setting, SettingBook
from magi.bus.library.local.tasksBook import (
    Channel,
    ChannelEnum,
    Task,
    TaskBook,
    TaskLastStatus,
    TaskRun,
    TaskRunBook,
    TaskSource,
)
from magi.bus.library.local.tokenUsageBook import TokenUsage, TokenUsageBook
from magi.bus.library.local.toolsBook import (
    ToolCatalogSnapshot,
    ToolCatalogState,
    ToolCatalogStateBook,
    ToolDefinition,
    ToolDefinitionBook,
)

__all__ = [
    "ActionItem",
    "ActionItemBook",
    "ActionPriority",
    "ActionSource",
    "Channel",
    "ChannelEnum",
    "Contact",
    "ContactBook",
    "ContactNote",
    "ContactNoteBook",
    "HookSignoff",
    "HookSignoffBook",
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
    "MCPTimeout",
    "Setting",
    "SettingBook",
    "Task",
    "TaskBook",
    "TaskLastStatus",
    "TaskRun",
    "TaskRunBook",
    "TaskSource",
    "TokenUsage",
    "TokenUsageBook",
    "ToolCatalogState",
    "ToolCatalogStateBook",
    "ToolCatalogSnapshot",
    "ToolDefinition",
    "ToolDefinitionBook",
]
