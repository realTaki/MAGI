"""Firmware shipped with BUS: concrete Books and Jobs.

Opening :class:`~magi.new_bus.bus.Bus` loads this set. Callers do not mount it.
"""

from typing import Any

from ..base.BaseJob import BaseJob, BaseJobBoard
from ..base.engine import EngineFactory
from ..base.heartbeat import Heartbeat
from .books.conversationBook import Conversation
from .books.messageBook import Message, MessageRole
from .jobs import (
    AppendMessageJob,
    AppendMessageJobBoard,
    AppendMessageResult,
    ArchiveMessagesJob,
    ArchiveMessagesJobBoard,
    ArchiveMessagesResult,
    CreateConversationJob,
    CreateConversationJobBoard,
    CreateConversationResult,
    ListConversationMessagesJob,
    ListConversationMessagesJobBoard,
    ListConversationMessagesResult,
    UpdateConversationSummaryJob,
    UpdateConversationSummaryJobBoard,
    UpdateConversationSummaryResult,
)


def create_job_boards(
    factory: EngineFactory, heartbeat: Heartbeat
) -> dict[type[BaseJob], BaseJobBoard[Any, Any, Any]]:
    """Create the fixed Board set shipped by Firmware."""
    return {
        CreateConversationJob: CreateConversationJobBoard(factory, heartbeat),
        AppendMessageJob: AppendMessageJobBoard(factory, heartbeat),
        ListConversationMessagesJob: ListConversationMessagesJobBoard(factory, heartbeat),
        ArchiveMessagesJob: ArchiveMessagesJobBoard(factory, heartbeat),
        UpdateConversationSummaryJob: UpdateConversationSummaryJobBoard(factory, heartbeat),
    }


__all__ = [
    "Conversation",
    "Message",
    "MessageRole",
    "AppendMessageJob",
    "AppendMessageJobBoard",
    "AppendMessageResult",
    "ArchiveMessagesJob",
    "ArchiveMessagesJobBoard",
    "ArchiveMessagesResult",
    "CreateConversationJob",
    "CreateConversationJobBoard",
    "CreateConversationResult",
    "ListConversationMessagesJob",
    "ListConversationMessagesJobBoard",
    "ListConversationMessagesResult",
    "UpdateConversationSummaryJob",
    "UpdateConversationSummaryJobBoard",
    "UpdateConversationSummaryResult",
]
