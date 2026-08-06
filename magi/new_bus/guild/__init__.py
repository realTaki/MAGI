"""new_bus.guild — 仅写。继承 BaseNotifyQueue 或 BaseJobQueue，override publish 即可。

Job 命名：动词打头（runAgentJob / sendA2AJob / chatJob / ...）。
Book 命名：名词结尾（contactBook / memoryBook / ...）。
"""

from magi.new_bus.guild.base import (
    MAX_ATTEMPTS,
    DEFAULT_LEASE_SECONDS,
    BaseNotifyQueue,
    BaseJobQueue,
    new_job_id,
)

# 往返 (publish → claim → submit_result)
from magi.new_bus.guild.chatJob import ChatJob, ChatJobResult, chatJob
from magi.new_bus.guild.callLLMJob import CallLLMJob, CallLLMResult, callLLMJob
from magi.new_bus.guild.runToolJob import RunToolJob, RunToolResult, runToolJob
from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult, deliveryJob
from magi.new_bus.guild.controlJob import ControlJob, ControlJobResult, controlJob
from magi.new_bus.guild.runAgentJob import RunAgentJob, RunAgentResult, runAgentJob
from magi.new_bus.guild.sendA2AJob import SendA2AJob, SendA2AResult, sendA2AJob

# 单向 (publish 直接落库)
from magi.new_bus.guild.setConfigJob import SetConfigJob, setConfigJob
from magi.new_bus.guild.setSettingJob import SetSettingJob, setSettingJob
from magi.new_bus.guild.scheduleTaskJob import ScheduleTaskJob, scheduleTaskJob
from magi.new_bus.guild.contactJob import ContactJob, contactJob
from magi.new_bus.guild.rememberJob import RememberJob, rememberJob

__all__ = [
    "BaseNotifyQueue",
    "BaseJobQueue",
    "MAX_ATTEMPTS", "DEFAULT_LEASE_SECONDS", "new_job_id",
    # 往返
    "ChatJob", "ChatJobResult", "chatJob",
    "CallLLMJob", "CallLLMResult", "callLLMJob",
    "RunToolJob", "RunToolResult", "runToolJob",
    "DeliveryJob", "DeliveryResult", "deliveryJob",
    "ControlJob", "ControlJobResult", "controlJob",
    "RunAgentJob", "RunAgentResult", "runAgentJob",
    "SendA2AJob", "SendA2AResult", "sendA2AJob",
    # 单向
    "SetConfigJob", "setConfigJob",
    "SetSettingJob", "setSettingJob",
    "ScheduleTaskJob", "scheduleTaskJob",
    "ContactJob", "contactJob",
    "RememberJob", "rememberJob",
]
