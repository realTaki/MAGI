"""MAGI-BUS vNext — software backplane.

Constructing :class:`Bus` starts Firmware with it. BaseBook fields live on the
record types (see :class:`Message`). Workers receive an identity-bound
:class:`BusForWorker` and never access BaseBooks directly.
"""

from .base.BaseBook import BaseRecord
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.dock import AndDock, OrDock
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
from .base.heartbeat import Slot
from .bus import Bus
from .bus_for_worker import BusForWorker, JobBoardClient
from .firmware import (
    AppendMessageJob,
    AppendMessageResult,
    ArchiveMessagesJob,
    ArchiveMessagesResult,
    Conversation,
    CreateConversationJob,
    CreateConversationResult,
    DeleteSettingJob,
    DeleteSettingResult,
    GetSettingJob,
    GetSettingResult,
    ListConversationMessagesJob,
    ListConversationMessagesResult,
    ListSettingsJob,
    ListSettingsResult,
    Message,
    MessageRole,
    SetSettingJob,
    SetSettingResult,
    Setting,
    UpdateConversationSummaryJob,
    UpdateConversationSummaryResult,
)

__all__ = [
    "BackendError",
    "BookNotFoundError",
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
    "OrDock",
    "AndDock",
    "Slot",
    "BusForWorker",
    "JobBoardClient",
    "Conversation",
    "Message",
    "MessageRole",
    "AppendMessageJob",
    "AppendMessageResult",
    "ArchiveMessagesJob",
    "ArchiveMessagesResult",
    "CreateConversationJob",
    "CreateConversationResult",
    "ListConversationMessagesJob",
    "ListConversationMessagesResult",
    "UpdateConversationSummaryJob",
    "UpdateConversationSummaryResult",
    "PostgresBackend",
    "SQLiteBackend",
    "Setting",
    "DeleteSettingJob",
    "DeleteSettingResult",
    "GetSettingJob",
    "GetSettingResult",
    "ListSettingsJob",
    "ListSettingsResult",
    "SetSettingJob",
    "SetSettingResult",
]
