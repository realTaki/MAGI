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
from magi.bus.library.local.hookSignoffBook import (
    HookSignoff,
    HookSignoffBook,
    HookSignoffStatus,
)
from magi.bus.library.local.mcpServerBook import (
    MCPConnectionType,
    McpServer,
    McpServerBook,
)
from magi.bus.library.local.memoryBook import (
    Memory,
    MemoryBook,
    MemoryKind,
)
from magi.bus.library.local.settingBook import (
    CHANNEL_OPTIONS_KEY,
    Setting,
    SettingBook,
)
from magi.bus.library.local.tasksBook import (
    Task,
    TaskBook,
    TaskRun,
    TaskRunBook,
    TaskRunStatus,
    TaskSource,
)
from magi.bus.library.local.tokenUsageBook import TokenUsage, TokenUsageBook
from magi.bus.library.local.toolsBook import (
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
