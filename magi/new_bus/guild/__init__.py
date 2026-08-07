"""new_bus.guild — 仅写。继承 BaseNotifyBoard 或 BaseJobBoard，override publish 即可。

Job 命名（双向，publish → claim → submit_result）：动词打头
（runAgentJobBoard / sendA2AJobBoard / chatJobBoard / ...）。
Notify 命名（单向，publish 直接落库）：动/名词打头
（contactNotifyBoard / rememberNotifyBoard / ...）。
Book 命名：名词结尾（contactBook / memoryBook / ...）。
"""

from magi.new_bus.guild.base import (
    MAX_ATTEMPTS,
    DEFAULT_LEASE_SECONDS,
    BaseNotifyBoard,
    BaseJobBoard,
    new_job_id,
)

# 往返 (publish → claim → submit_result)
from magi.new_bus.guild.chatJob import ChatJob, ChatJobResult, chatJobBoard
from magi.new_bus.guild.callLLMJob import CallLLMJob, CallLLMResult, callLLMJobBoard
from magi.new_bus.guild.runToolJob import RunToolJob, RunToolResult, runToolJobBoard
from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult, deliveryJobBoard
from magi.new_bus.guild.controlJob import ControlJob, ControlJobResult, controlJobBoard
from magi.new_bus.guild.runAgentJob import RunAgentJob, RunAgentResult, runAgentJobBoard
from magi.new_bus.guild.sendA2AJob import SendA2AJob, SendA2AResult, sendA2AJobBoard
from magi.new_bus.guild.providerConfigJob import (
    ProviderConfigJob,
    ProviderConfigResult,
    providerConfigJobBoard,
)

# 单向 (publish 直接落库)
from magi.new_bus.guild.setConfigNotify import SetConfigNotify, setConfigNotifyBoard
from magi.new_bus.guild.setSettingNotify import SetSettingNotify, setSettingNotifyBoard
from magi.new_bus.guild.scheduleTaskNotify import ScheduleTaskNotify, scheduleTaskNotifyBoard
from magi.new_bus.guild.contactNotify import ContactNotify, contactNotifyBoard
from magi.new_bus.guild.rememberNotify import RememberNotify, rememberNotifyBoard

__all__ = [
    "BaseNotifyBoard",
    "BaseJobBoard",
    "MAX_ATTEMPTS", "DEFAULT_LEASE_SECONDS", "new_job_id",
    # 往返
    "ChatJob", "ChatJobResult", "chatJobBoard",
    "CallLLMJob", "CallLLMResult", "callLLMJobBoard",
    "RunToolJob", "RunToolResult", "runToolJobBoard",
    "DeliveryJob", "DeliveryResult", "deliveryJobBoard",
    "ControlJob", "ControlJobResult", "controlJobBoard",
    "RunAgentJob", "RunAgentResult", "runAgentJobBoard",
    "SendA2AJob", "SendA2AResult", "sendA2AJobBoard",
    "ProviderConfigJob", "ProviderConfigResult", "providerConfigJobBoard",
    # 单向 (Notify)
    "SetConfigNotify", "setConfigNotifyBoard",
    "SetSettingNotify", "setSettingNotifyBoard",
    "ScheduleTaskNotify", "scheduleTaskNotifyBoard",
    "ContactNotify", "contactNotifyBoard",
    "RememberNotify", "rememberNotifyBoard",
]
