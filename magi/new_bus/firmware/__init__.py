"""Firmware shipped with BUS: concrete Books and Jobs.

Opening :class:`~magi.new_bus.bus.Bus` loads this set. Callers do not mount it.
"""

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


def attach(bus) -> None:
    """Bind semantic chat contracts onto a Bus. Called by Bus at start."""
    bus.mount_job(CreateConversationJob, board_cls=CreateConversationJobBoard)
    bus.mount_job(AppendMessageJob, board_cls=AppendMessageJobBoard)
    bus.mount_job(ListConversationMessagesJob, board_cls=ListConversationMessagesJobBoard)
    bus.mount_job(ArchiveMessagesJob, board_cls=ArchiveMessagesJobBoard)
    bus.mount_job(UpdateConversationSummaryJob, board_cls=UpdateConversationSummaryJobBoard)


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
