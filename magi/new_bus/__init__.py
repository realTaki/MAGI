"""MAGI-BUS vNext — software backplane.

Constructing :class:`Bus` starts Firmware with it. BaseBook fields live on the
record types (see :class:`Message`). External code talks to Bus, never to BaseBook.
"""

from .base.backends.file import FileBackend
from .base.backends.postgres import PostgresBackend
from .base.backends.sqlite import SQLiteBackend
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.BaseRecord import BaseRecord
from .base.errors import (
    BackendError,
    BookNotFoundError,
    BusError,
    InvalidJobError,
    InvalidJobStateError,
    JobNotFoundError,
    SlotNotFoundError,
    SlotOccupiedError,
    SlotRejected,
)
from .base.manageBookJob import BookOp, ManageBookJob
from .base.slot import Slot
from .bus import Bus
from .firmware import (
    Conversation,
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
    "InvalidJobError",
    "InvalidJobStateError",
    "BaseJob",
    "BaseJobResult",
    "BaseJobBoard",
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
