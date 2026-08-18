"""Firmware shipped with BUS: concrete Books and Jobs.

Opening :class:`~magi.new_bus.bus.Bus` loads this set. Callers do not mount it.
"""

from .books.conversationBook import Conversation
from .books.messageBook import Message
from .jobs.manageConversationJob import ManageConversationJob, ManageConversationJobBoard
from .jobs.manageMessageJob import ManageMessageJob, ManageMessageJobBoard
from .version import FIRMWARE_VERSION, FirmwareVersion


def attach(bus) -> None:
    """Bind this Firmware onto a Bus. Called by Bus itself at start."""
    from .books.conversationBook import ConversationBook
    from .books.messageBook import MessageBook

    bus.mount_book(ConversationBook.NAME, book_cls=ConversationBook)
    bus.mount_book(MessageBook.NAME, book_cls=MessageBook)
    bus.mount_job(ManageConversationJob, board_cls=ManageConversationJobBoard)
    bus.mount_job(ManageMessageJob, board_cls=ManageMessageJobBoard)


__all__ = [
    "FIRMWARE_VERSION",
    "FirmwareVersion",
    "Conversation",
    "ManageConversationJob",
    "ManageConversationJobBoard",
    "ManageMessageJob",
    "ManageMessageJobBoard",
    "Message",
]
