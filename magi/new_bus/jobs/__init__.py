"""new_bus.jobs — 每种 Job 一个文件。public 层: Job + Result dataclass; internal 层: _Row ORM。"""

from magi.new_bus.jobs.base import MAX_ATTEMPTS, DEFAULT_LEASE_SECONDS, BaseJobQueue
from magi.new_bus.jobs.ConfigJob import ConfigJob, ConfigJobQueue, ConfigJobResult
from magi.new_bus.jobs.LLMJob import LLMJob, LLMJobQueue, LLMJobResult

__all__ = [
    "BaseJobQueue",
    "MAX_ATTEMPTS",
    "DEFAULT_LEASE_SECONDS",
    "ConfigJob",
    "ConfigJobResult",
    "ConfigJobQueue",
    "LLMJob",
    "LLMJobResult",
    "LLMJobQueue",
]
