"""new_bus.jobs — 每种 Job 独立一个文件，包含 定义 + Result + publish + claim。"""

from magi.new_bus.jobs.ConfigJob import ConfigJob, ConfigJobResult, publish_config_job, claim_config_job
from magi.new_bus.jobs.LLMJob import LLMJob, LLMJobResult, publish_llm_job, claim_llm_job

__all__ = [
    "ConfigJob",
    "ConfigJobResult",
    "publish_config_job",
    "claim_config_job",
    "LLMJob",
    "LLMJobResult",
    "publish_llm_job",
    "claim_llm_job",
]
