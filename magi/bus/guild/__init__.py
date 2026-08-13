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
from magi.bus.guild.base import BaseJobBoard

# 往返 (publish → claim → submit_result)
from magi.bus.guild.callLLMJob import CallLLMJob, CallLLMResult, LLMErrorCode, callLLMJobBoard
from magi.bus.guild.changeProviderConfigJob import (
    ChangeProviderConfigJob,
    ChangeProviderConfigResult,
    changeProviderConfigJobBoard,
)
from magi.bus.guild.chatJob import ChatJob, ChatJobResult, chatJobBoard
from magi.bus.guild.deliveryJob import DeliveryJob, DeliveryResult, deliveryJobBoard
from magi.bus.guild.mcpServerChangedJob import (
    MCPKind,
    McpServerChangedJob,
    McpServerChangedResult,
    mcpServerChangedJobBoard,
)
from magi.bus.guild.runTaskJob import RunTaskJob, RunTaskResult, runTaskJobBoard
from magi.bus.guild.runToolJob import RunToolJob, RunToolResult, runToolJobBoard
from magi.bus.guild.seedPresetTasksJob import (
    SeedPresetTaskJob,
    SeedPresetTaskResult,
    seedPresetTaskJobBoard,
)

__all__ = [
    "BaseJobBoard",
    # 往返
    "CallLLMJob",
    "CallLLMResult",
    "LLMErrorCode",
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
    "MCPKind",
    "McpServerChangedJob",
    "McpServerChangedResult",
    "mcpServerChangedJobBoard",
    "SeedPresetTaskJob",
    "SeedPresetTaskResult",
    "seedPresetTaskJobBoard",
    "RunTaskJob",
    "RunTaskResult",
    "runTaskJobBoard",
]
