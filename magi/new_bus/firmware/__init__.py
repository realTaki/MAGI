"""Firmware shipped with BUS: concrete Books and Jobs.

Opening :class:`~magi.new_bus.bus.Bus` loads this set. Callers do not mount it.
"""

from .books.messageBook import Message
from .jobs.manageMessageJob import ManageMessageJob, ManageMessageJobBoard
from .version import FIRMWARE_VERSION


def attach(bus) -> None:
    """Bind this Firmware onto a Bus. Called by Bus itself at start."""
    from .books.messageBook import MessageBook

    bus.mount_book(MessageBook.NAME, book_cls=MessageBook)
    bus.mount_job(ManageMessageJob, board_cls=ManageMessageJobBoard)


__all__ = [
    "FIRMWARE_VERSION",
    "ManageMessageJob",
    "ManageMessageJobBoard",
    "Message",
]
