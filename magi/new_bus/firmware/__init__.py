"""Firmware shipped with BUS: concrete Books and Jobs.

Opening :class:`~magi.new_bus.bus.Bus` loads this set. Callers do not mount it.
"""

from typing import Any

from ..base.BaseJob import BaseJob, BaseJobBoard
from ..base.engine import EngineFactory
from ..base.heartbeat import Heartbeat
from .books.conversationBook import Conversation
from .books.messageBook import Message, MessageRole
from .books.settingsBook import Setting
from .books.tokenUsageBook import TokenUsage
from .jobs import (
    AppendMessageJob,
    AppendMessageJobBoard,
    AppendMessageResult,
    ArchiveMessagesJob,
    ArchiveMessagesJobBoard,
    ArchiveMessagesResult,
    CallLLMJob,
    CallLLMJobBoard,
    CallLLMResult,
    CreateConversationJob,
    CreateConversationJobBoard,
    CreateConversationResult,
    DeleteSettingJob,
    DeleteSettingJobBoard,
    DeleteSettingResult,
    GetSettingJob,
    GetSettingJobBoard,
    GetSettingResult,
    ListConversationMessagesJob,
    ListConversationMessagesJobBoard,
    ListConversationMessagesResult,
    ListSettingsJob,
    ListSettingsJobBoard,
    ListSettingsResult,
    LLMErrorCode,
    RecordTokenUsageJob,
    RecordTokenUsageJobBoard,
    RecordTokenUsageResult,
    SetSettingJob,
    SetSettingJobBoard,
    SetSettingResult,
    UpdateConversationSummaryJob,
    UpdateConversationSummaryJobBoard,
    UpdateConversationSummaryResult,
)


def create_job_boards(
    factory: EngineFactory, heartbeat: Heartbeat
) -> dict[type[BaseJob], BaseJobBoard[Any, Any, Any]]:
    """Create the fixed Board set shipped by Firmware."""
    return {
        CreateConversationJob: CreateConversationJobBoard(factory, heartbeat),
        AppendMessageJob: AppendMessageJobBoard(factory, heartbeat),
        ListConversationMessagesJob: ListConversationMessagesJobBoard(factory, heartbeat),
        ArchiveMessagesJob: ArchiveMessagesJobBoard(factory, heartbeat),
        UpdateConversationSummaryJob: UpdateConversationSummaryJobBoard(factory, heartbeat),
        GetSettingJob: GetSettingJobBoard(factory, heartbeat),
        SetSettingJob: SetSettingJobBoard(factory, heartbeat),
        DeleteSettingJob: DeleteSettingJobBoard(factory, heartbeat),
        ListSettingsJob: ListSettingsJobBoard(factory, heartbeat),
        CallLLMJob: CallLLMJobBoard(factory, heartbeat),
        RecordTokenUsageJob: RecordTokenUsageJobBoard(factory, heartbeat),
    }


__all__ = [
    "Conversation",
    "Message",
    "MessageRole",
    "Setting",
    "TokenUsage",
    "AppendMessageJob",
    "AppendMessageJobBoard",
    "AppendMessageResult",
    "ArchiveMessagesJob",
    "ArchiveMessagesJobBoard",
    "ArchiveMessagesResult",
    "CreateConversationJob",
    "CreateConversationJobBoard",
    "CreateConversationResult",
    "ListConversationMessagesJob",
    "ListConversationMessagesJobBoard",
    "ListConversationMessagesResult",
    "UpdateConversationSummaryJob",
    "UpdateConversationSummaryJobBoard",
    "UpdateConversationSummaryResult",
    "DeleteSettingJob",
    "DeleteSettingJobBoard",
    "DeleteSettingResult",
    "GetSettingJob",
    "GetSettingJobBoard",
    "GetSettingResult",
    "ListSettingsJob",
    "ListSettingsJobBoard",
    "ListSettingsResult",
    "SetSettingJob",
    "SetSettingJobBoard",
    "SetSettingResult",
    "CallLLMJob",
    "CallLLMJobBoard",
    "CallLLMResult",
    "LLMErrorCode",
    "RecordTokenUsageJob",
    "RecordTokenUsageJobBoard",
    "RecordTokenUsageResult",
]
