"""MAGI-BUS vNext — software backplane.

Constructing :class:`Bus` starts Firmware with it. BaseBook fields live on the
record types (see :class:`Message`). External code talks to Bus, never to BaseBook.
"""

from .base.BaseBook import BaseRecord
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.engine import EngineFactory, PostgresBackend, SQLiteBackend
from .base.errors import (
    BackendError,
    BookNotFoundError,
    BusError,
    InvalidJobError,
    InvalidJobStateError,
    JobNotFoundError,
)
from .base.file import FileBackend
from .base.openBookJob import BookOp, OpenBookJob
from .bus import Bus
from .firmware import (
    Conversation,
    OpenConversationBookJob,
    OpenConversationBookJobBoard,
    OpenMessageBookJob,
    OpenMessageBookJobBoard,
    Message,
)

__all__ = [
    "BackendError",
    "OpenBookJob",
    "BookNotFoundError",
    "BookOp",
    "BaseRecord",
    "Bus",
    "BusError",
    "EngineFactory",
    "FileBackend",
    "InvalidJobError",
    "InvalidJobStateError",
    "BaseJob",
    "BaseJobResult",
    "BaseJobBoard",
    "JobNotFoundError",
    "JobStatus",
    "Conversation",
    "OpenConversationBookJob",
    "OpenConversationBookJobBoard",
    "OpenMessageBookJob",
    "OpenMessageBookJobBoard",
    "Message",
    "PostgresBackend",
    "SQLiteBackend",
]
