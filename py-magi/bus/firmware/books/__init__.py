"""Concrete Books. Not part of the Firmware public surface."""
from .contactBook import (
    MAGI_CONTACT_ID,
    SYSTEM_CONTACT_ID,
    Contact,
    ContactBook,
    ContactRole,
)
from .contactNoteBook import ContactNote, ContactNoteBook, NoteKind
from .conversationBook import Conversation, ConversationBook
from .memoryBook import Memory, MemoryBook, MemoryKind
from .messageBook import Message, MessageBook
from .promptsBook import KNOWN_PROMPTS, PromptsBook
from .settingsBook import Setting, SettingsBook
from .skillsBook import Skill, SkillsBook
from .taskBook import Task, TaskBook, TaskSource
from .toolsBook import Tool, ToolsBook

__all__ = [
    "Contact",
    "ContactBook",
    "ContactRole",
    "MAGI_CONTACT_ID",
    "SYSTEM_CONTACT_ID",
    "ContactNote",
    "ContactNoteBook",
    "NoteKind",
    "Conversation",
    "ConversationBook",
    "Memory",
    "MemoryBook",
    "MemoryKind",
    "Message",
    "MessageBook",
    "Setting",
    "SettingsBook",
    "KNOWN_PROMPTS",
    "PromptsBook",
    "Skill",
    "SkillsBook",
    "Task",
    "TaskBook",
    "TaskSource",
    "Tool",
    "ToolsBook",
]
