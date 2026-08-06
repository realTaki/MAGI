"""new_bus.jobs — 每种 Job 一个文件。public: Job + Result dataclass; internal: _Row ORM。"""

from magi.new_bus.jobs.base import MAX_ATTEMPTS, DEFAULT_LEASE_SECONDS, BaseJobQueue
from magi.new_bus.jobs.ChatJob import ChatJob, ChatJobQueue, ChatJobResult
from magi.new_bus.jobs.ConfigJob import ConfigJob, ConfigJobQueue, ConfigJobResult
from magi.new_bus.jobs.LLMJob import LLMJob, LLMJobQueue, LLMJobResult

__all__ = [
    "BaseJobQueue",
    "MAX_ATTEMPTS",
    "DEFAULT_LEASE_SECONDS",
    "ChatJob",
    "ChatJobResult",
    "ChatJobQueue",
    "ConfigJob",
    "ConfigJobResult",
    "ConfigJobQueue",
    "LLMJob",
    "LLMJobResult",
    "LLMJobQueue",
]
