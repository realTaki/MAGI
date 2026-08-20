"""Firmware shipped with BUS: concrete Books and Jobs.

Opening :class:`~magi.new_bus.bus.Bus` loads this set. Callers do not mount it.
"""

from .books.conversationBook import Conversation
from .books.messageBook import Message
from .jobs.openConversationBookJob import OpenConversationBookJob, OpenConversationBookJobBoard
from .jobs.openMessageBookJob import OpenMessageBookJob, OpenMessageBookJobBoard


def attach(bus) -> None:
    """Bind this Firmware onto a Bus. Called by Bus itself at start."""
    bus.mount_book(OpenConversationBookJobBoard)
    bus.mount_book(OpenMessageBookJobBoard)


__all__ = [
    "Conversation",
    "OpenConversationBookJob",
    "OpenConversationBookJobBoard",
    "OpenMessageBookJob",
    "OpenMessageBookJobBoard",
    "Message",
]
