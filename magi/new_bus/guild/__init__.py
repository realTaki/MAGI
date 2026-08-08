"""new_bus.guild — 仅写。继承 BaseJobBoard，override publish 即可。

Job 命名（publish → claim → submit_result）：动词打头
（runAgentJobBoard / sendA2AJobBoard / callLLMJobBoard / ...）。
Book 命名：名词结尾（memoryBook / contactBook / ...）。
"""

from magi.new_bus.guild.base import (
    MAX_ATTEMPTS,
    DEFAULT_LEASE_SECONDS,
    BaseNotifyBoard,
    BaseJobBoard,
    new_job_id,
)

# 往返 (publish → claim → submit_result)
from magi.new_bus.guild.callLLMJob import CallLLMJob, CallLLMResult, callLLMJobBoard
from magi.new_bus.guild.runToolJob import RunToolJob, RunToolResult, runToolJobBoard
from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult, deliveryJobBoard
from magi.new_bus.guild.runAgentJob import RunAgentJob, RunAgentResult, runAgentJobBoard
from magi.new_bus.guild.sendA2AJob import SendA2AJob, SendA2AResult, sendA2AJobBoard
from magi.new_bus.guild.changeProviderConfigJob import (
    ChangeProviderConfigJob,
    ChangeProviderConfigResult,
    changeProviderConfigJobBoard,
)
from magi.new_bus.guild.mcpServerChangedJob import (
    McpServerChangedJob,
    McpServerChangedResult,
    VALID_KINDS as _MCP_CHANGED_KINDS,
    mcpServerChangedJobBoard,
)
from magi.new_bus.guild.seedPresetTasksJob import (
    SeedPresetTasksJob,
    SeedPresetTasksResult,
    seedPresetTasksJobBoard,
)

__all__ = [
    "BaseNotifyBoard",
    "BaseJobBoard",
    "MAX_ATTEMPTS", "DEFAULT_LEASE_SECONDS", "new_job_id",
    # 往返
    "CallLLMJob", "CallLLMResult", "callLLMJobBoard",
    "RunToolJob", "RunToolResult", "runToolJobBoard",
    "DeliveryJob", "DeliveryResult", "deliveryJobBoard",
    "RunAgentJob", "RunAgentResult", "runAgentJobBoard",
    "SendA2AJob", "SendA2AResult", "sendA2AJobBoard",
    "ChangeProviderConfigJob", "ChangeProviderConfigResult", "changeProviderConfigJobBoard",
    "McpServerChangedJob", "McpServerChangedResult", "mcpServerChangedJobBoard",
    "_MCP_CHANGED_KINDS",
    "SeedPresetTasksJob", "SeedPresetTasksResult", "seedPresetTasksJobBoard",
]
