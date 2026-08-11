"""bus.guild — 仅写。继承 BaseJobBoard，override publish 即可。

Job 命名（publish → claim → submit_result）：动词打头
（chatJob / a2aRequestJobBoard / callLLMJobBoard / ...）。
Book 命名：名词结尾（memoryBook / contactBook / ...）。
"""

from magi.bus.guild.a2aJob import (
    A2ANotifyJob,
    A2ANotifyResult,
    A2ARequestJob,
    A2ARequestResult,
    a2aNotifyBoard,
    a2aRequestJobBoard,
)
from magi.bus.guild.base import (
    DEFAULT_LEASE_SECONDS,
    MAX_ATTEMPTS,
    BaseJobBoard,
    BaseNotifyBoard,
    new_job_id,
)

# 往返 (publish → claim → submit_result)
from magi.bus.guild.callLLMJob import CallLLMJob, CallLLMResult, callLLMJobBoard
from magi.bus.guild.changeProviderConfigJob import (
    ChangeProviderConfigJob,
    ChangeProviderConfigResult,
    changeProviderConfigJobBoard,
)
from magi.bus.guild.chatJob import ChatJob, ChatJobResult, chatJobBoard, publish_chat
from magi.bus.guild.deliveryJob import DeliveryJob, DeliveryResult, deliveryJobBoard
from magi.bus.guild.mcpServerChangedJob import (
    VALID_KINDS as _MCP_CHANGED_KINDS,
)
from magi.bus.guild.mcpServerChangedJob import (
    McpServerChangedJob,
    McpServerChangedResult,
    mcpServerChangedJobBoard,
)
from magi.bus.guild.runTaskJob import RunTaskJob, RunTaskResult, runTaskJobBoard
from magi.bus.guild.runToolJob import RunToolJob, RunToolResult, runToolJobBoard
from magi.bus.guild.seedPresetTasksJob import (
    SeedPresetTasksJob,
    SeedPresetTasksResult,
    seedPresetTasksJobBoard,
)

__all__ = [
    "BaseNotifyBoard",
    "BaseJobBoard",
    "MAX_ATTEMPTS",
    "DEFAULT_LEASE_SECONDS",
    "new_job_id",
    # 往返
    "CallLLMJob",
    "CallLLMResult",
    "callLLMJobBoard",
    "RunToolJob",
    "RunToolResult",
    "runToolJobBoard",
    "DeliveryJob",
    "DeliveryResult",
    "deliveryJobBoard",
    "ChatJob",
    "ChatJobResult",
    "chatJobBoard",
    "publish_chat",
    "A2ARequestJob",
    "A2ARequestResult",
    "a2aRequestJobBoard",
    "A2ANotifyJob",
    "A2ANotifyResult",
    "a2aNotifyBoard",
    "ChangeProviderConfigJob",
    "ChangeProviderConfigResult",
    "changeProviderConfigJobBoard",
    "McpServerChangedJob",
    "McpServerChangedResult",
    "mcpServerChangedJobBoard",
    "_MCP_CHANGED_KINDS",
    "SeedPresetTasksJob",
    "SeedPresetTasksResult",
    "seedPresetTasksJobBoard",
    "RunTaskJob",
    "RunTaskResult",
    "runTaskJobBoard",
]
