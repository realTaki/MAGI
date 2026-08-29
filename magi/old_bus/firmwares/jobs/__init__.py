"""bus.firmwares.jobs — concrete Job Boards.

Each board inherits :class:`~magi.bus.bases.job.BaseJobBoard` and
overrides ``publish`` when it needs domain checks. Job names are
verb-led (``chatNotifyBoard`` / ``a2aRequestJobBoard`` / …).
"""

from magi.old_bus.firmwares.jobs.a2aJob import (
    A2ANotifyJob,
    A2ANotifyResult,
    A2ARequestJob,
    A2ARequestResult,
    a2aNotifyBoard,
    a2aRequestJobBoard,
)
from magi.old_bus.firmwares.jobs.callLLMJob import (
    CallLLMJob,
    CallLLMResult,
    LLMErrorCode,
    callLLMJobBoard,
)
from magi.old_bus.firmwares.jobs.changeMCPServerJob import (
    ChangeMCPServerJob,
    ChangeMCPServerResult,
    MCPKind,
    changeMCPServerJobBoard,
)
from magi.old_bus.firmwares.jobs.changeProviderConfigJob import (
    ChangeProviderConfigJob,
    ChangeProviderConfigResult,
    changeProviderConfigJobBoard,
)
from magi.old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob, ChatNotifyResult, chatNotifyBoard
from magi.old_bus.firmwares.jobs.deliveryNotifyJob import (
    DeliveryNotifyJob,
    DeliveryNotifyResult,
    deliveryNotifyJobBoard,
)
from magi.old_bus.firmwares.jobs.runTaskJob import RunTaskJob, RunTaskResult, runTaskJobBoard
from magi.old_bus.firmwares.jobs.runToolJob import RunToolJob, RunToolResult, runToolJobBoard
from magi.old_bus.firmwares.jobs.seedPresetTasksJob import (
    SeedPresetTaskJob,
    SeedPresetTaskResult,
    seedPresetTaskJobBoard,
)

__all__ = [
    "CallLLMJob",
    "CallLLMResult",
    "LLMErrorCode",
    "callLLMJobBoard",
    "RunToolJob",
    "RunToolResult",
    "runToolJobBoard",
    "DeliveryNotifyJob",
    "DeliveryNotifyResult",
    "deliveryNotifyJobBoard",
    "ChatNotifyJob",
    "ChatNotifyResult",
    "chatNotifyBoard",
    "A2ARequestJob",
    "A2ARequestResult",
    "a2aRequestJobBoard",
    "A2ANotifyJob",
    "A2ANotifyResult",
    "a2aNotifyBoard",
    "ChangeProviderConfigJob",
    "ChangeProviderConfigResult",
    "changeProviderConfigJobBoard",
    "MCPKind",
    "ChangeMCPServerJob",
    "ChangeMCPServerResult",
    "changeMCPServerJobBoard",
    "SeedPresetTaskJob",
    "SeedPresetTaskResult",
    "seedPresetTaskJobBoard",
    "RunTaskJob",
    "RunTaskResult",
    "runTaskJobBoard",
]
