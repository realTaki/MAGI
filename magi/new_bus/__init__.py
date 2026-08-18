"""MAGI-BUS vNext — software backplane.

Base defines mechanisms. Firmware (later) will define concrete Books and Jobs.
External code talks to :class:`Bus`, never to Book or Backend.
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
    "Job",
    "JobAlreadyClaimedError",
    "JobNotFoundError",
    "JobStatus",
    "PostgresBackend",
    "SQLiteBackend",
    "Slot",
    "SlotNotFoundError",
    "SlotOccupiedError",
    "SlotRejected",
]
