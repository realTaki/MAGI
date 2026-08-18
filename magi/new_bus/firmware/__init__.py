"""Firmware: concrete Books and Jobs on top of Base.

External code depends on this protocol, not on other modules.
"""

from ..bus import Bus
from .jobs.manageMessageJob import ManageMessageJob, ManageMessageJobBoard
from .version import FIRMWARE_VERSION


def install(bus: Bus) -> None:
    """Mount this Firmware's Books and claimable JobBoards."""
    from .books.messageBook import MessageBook

    bus.mount_book(MessageBook.NAME, book_cls=MessageBook)
    bus.mount_job(ManageMessageJob, board_cls=ManageMessageJobBoard)


__all__ = [
    "FIRMWARE_VERSION",
    "ManageMessageJob",
    "ManageMessageJobBoard",
    "install",
]
