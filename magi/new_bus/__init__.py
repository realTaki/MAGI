"""MAGI-BUS vNext — software backplane.

Constructing :class:`Bus` starts Firmware with it. BaseBook fields live on the
record types (see :class:`Message`). External code talks to Bus, never to BaseBook.
"""

from .base.backends.file import FileBackend
from .base.backends.postgres import PostgresBackend
from .base.backends.sqlite import SQLiteBackend
from .base.BaseBook import BaseRecord
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.errors import (
    BackendError,
    BookNotFoundError,
    BusError,
    FirmwareCompatibilityError,
    InvalidJobError,
    InvalidJobStateError,
    JobAlreadyClaimedError,
    JobNotFoundError,
    SlotNotFoundError,
    SlotOccupiedError,
    SlotRejected,
)
from .base.manageBookJob import BookOp, ManageBookJob
from .base.slot import Slot
from .bus import Bus
from .firmware import (
    FIRMWARE_VERSION,
    Conversation,
    FirmwareVersion,
    ManageConversationJob,
    ManageConversationJobBoard,
    ManageMessageJob,
    ManageMessageJobBoard,
    Message,
)

__all__ = [
    "BackendError",
    "ManageBookJob",
    "BookNotFoundError",
    "BookOp",
    "BaseRecord",
    "Bus",
    "BusError",
    "FileBackend",
    "FirmwareCompatibilityError",
    "InvalidJobError",
    "InvalidJobStateError",
    "FIRMWARE_VERSION",
    "FirmwareVersion",
    "BaseJob",
    "BaseJobBoard",
    "BaseJobResult",
    "JobAlreadyClaimedError",
    "JobNotFoundError",
    "JobStatus",
    "Conversation",
    "ManageConversationJob",
    "ManageConversationJobBoard",
    "ManageMessageJob",
    "ManageMessageJobBoard",
    "Message",
    "PostgresBackend",
    "SQLiteBackend",
    "Slot",
    "SlotNotFoundError",
    "SlotOccupiedError",
    "SlotRejected",
]
