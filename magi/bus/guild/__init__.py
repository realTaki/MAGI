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
from magi.bus.guild.chatJob import ChatJob, ChatJobResult, chatJobBoard
from magi.bus.guild.deliveryJob import DeliveryJob, DeliveryResult, deliveryJobBoard
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
    "SeedPresetTasksJob",
    "SeedPresetTasksResult",
    "seedPresetTasksJobBoard",
    "RunTaskJob",
    "RunTaskResult",
    "runTaskJobBoard",
]
