"""MAGI-BUS vNext — software backplane.

Constructing :class:`Bus` starts Firmware with it. Book fields live on the
record types (see :class:`Message`). External code talks to Bus, never to Book.
"""

from .base.backends.file import FileBackend
from .base.backends.postgres import PostgresBackend
from .base.backends.sqlite import SQLiteBackend
from .base.job import BookOp, Job, JobStatus, ManageBookJob
from .base.slot import Slot
from .bus import Bus
from .errors import (
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
from .firmware import FIRMWARE_VERSION, ManageMessageJob, ManageMessageJobBoard, Message

__all__ = [
    "BackendError",
    "ManageBookJob",
    "BookNotFoundError",
    "BookOp",
    "Bus",
    "BusError",
    "FileBackend",
    "FirmwareCompatibilityError",
    "InvalidJobError",
    "InvalidJobStateError",
    "FIRMWARE_VERSION",
    "Job",
    "JobAlreadyClaimedError",
    "JobNotFoundError",
    "JobStatus",
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
