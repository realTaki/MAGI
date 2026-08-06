"""new_bus.jobs — 仅写。继承 BaseJobQueue，override publish 即可。"""

from magi.new_bus.jobs.base import MAX_ATTEMPTS, DEFAULT_LEASE_SECONDS, BaseJobQueue

# 异步 (publish → claim → submit_result)
from magi.new_bus.jobs.ChatJob import ChatJob, ChatJobQueue, ChatJobResult
from magi.new_bus.jobs.LLMJob import LLMJob, LLMJobQueue, LLMJobResult
from magi.new_bus.jobs.ToolJob import ToolJob, ToolJobQueue, ToolJobResult
from magi.new_bus.jobs.DeliveryJob import DeliveryJob, DeliveryJobQueue, DeliveryJobResult
from magi.new_bus.jobs.ControlJob import ControlJob, ControlJobQueue, ControlJobResult

# 同步 (publish 直接落库)
from magi.new_bus.jobs.ConfigJob import ConfigJob, ConfigJobQueue
from magi.new_bus.jobs.SettingsJob import SettingsJob, SettingsJobQueue
from magi.new_bus.jobs.TaskJob import TaskJob, TaskJobQueue
from magi.new_bus.jobs.ContactJob import ContactJob, ContactJobQueue
from magi.new_bus.jobs.MemoryJob import MemoryJob, MemoryJobQueue

__all__ = [
    "BaseJobQueue",
    "MAX_ATTEMPTS", "DEFAULT_LEASE_SECONDS",
    "ChatJob", "ChatJobResult", "ChatJobQueue",
    "LLMJob", "LLMJobResult", "LLMJobQueue",
    "ToolJob", "ToolJobResult", "ToolJobQueue",
    "DeliveryJob", "DeliveryJobResult", "DeliveryJobQueue",
    "ControlJob", "ControlJobResult", "ControlJobQueue",
    "ConfigJob", "ConfigJobQueue",
    "SettingsJob", "SettingsJobQueue",
    "TaskJob", "TaskJobQueue",
    "ContactJob", "ContactJobQueue",
    "MemoryJob", "MemoryJobQueue",
]
