"""new_bus.guild — 仅写。继承 BaseNotifyBoard 或 BaseJobBoard，override publish 即可。

Job 命名：动词打头（runAgentJobBoard / sendA2AJobBoard / chatJobBoard / ...）。
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
from magi.new_bus.guild.chatJobBoard import ChatJob, ChatJobResult, chatJobBoard
from magi.new_bus.guild.callLLMJobBoard import CallLLMJob, CallLLMResult, callLLMJobBoard
from magi.new_bus.guild.runToolJobBoard import RunToolJob, RunToolResult, runToolJobBoard
from magi.new_bus.guild.deliveryJobBoard import DeliveryJob, DeliveryResult, deliveryJobBoard
from magi.new_bus.guild.controlJobBoard import ControlJob, ControlJobResult, controlJobBoard
from magi.new_bus.guild.runAgentJobBoard import RunAgentJob, RunAgentResult, runAgentJobBoard
from magi.new_bus.guild.sendA2AJobBoard import SendA2AJob, SendA2AResult, sendA2AJobBoard

# 单向 (publish 直接落库)
from magi.new_bus.guild.setConfigJobBoard import SetConfigJob, setConfigJobBoard
from magi.new_bus.guild.setSettingJobBoard import SetSettingJob, setSettingJobBoard
from magi.new_bus.guild.scheduleTaskJobBoard import ScheduleTaskJob, scheduleTaskJobBoard
from magi.new_bus.guild.contactJobBoard import ContactJob, contactJobBoard
from magi.new_bus.guild.rememberJobBoard import RememberJob, rememberJobBoard

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
    # 单向
    "SetConfigJob", "setConfigJobBoard",
    "SetSettingJob", "setSettingJobBoard",
    "ScheduleTaskJob", "scheduleTaskJobBoard",
    "ContactJob", "contactJobBoard",
    "RememberJob", "rememberJobBoard",
]
