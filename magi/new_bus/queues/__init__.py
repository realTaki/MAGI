"""new_bus.queues — durable job queue layer.

Each Queue wraps one (or a small group of) ORM tables and provides
``publish / claim / submit_result / get_result`` semantics, plus
optional ``inline=True`` publish for single-direction writes.

Queues
======

- :class:`LLMJobQueue`            — provider-worker LLM inference (``llm_attempts``)
- :class:`ConfigJobQueue`         — transient provider-config refresh (``control_jobs``), supports inline
- :class:`ChatJobQueue`           — chat message queue (``chat_jobs``)
- :class:`AgentRunQueue`          — agent turn queue (``agent_inbox``)
- :class:`ToolJobQueue`           — tool execution queue (``tool_jobs``)
- :class:`DeliveryQueue`          — channel delivery outbox (``deliveries``), supports inline
- :class:`A2AInvocationQueue`     — peer-MAGI call lifecycle (``a2a_invocations``)
"""

from magi.new_bus.queues.a2a import (
    A2AInvocationJob,
    A2AInvocationQueue,
    A2AInvocationResult,
)
from magi.new_bus.queues.agent_run import (
    AgentRunJob,
    AgentRunQueue,
    AgentRunResult,
)
from magi.new_bus.queues.base import (
    DEFAULT_LEASE_SECONDS,
    INLINE_PUBLISHER,
    MAX_ATTEMPTS,
    BaseJobQueue,
    new_job_id,
)
from magi.new_bus.queues.chat import (
    ChatJob,
    ChatJobQueue,
    ChatJobResult,
)
from magi.new_bus.queues.config import (
    ConfigJob,
    ConfigJobQueue,
    ConfigJobResult,
)
from magi.new_bus.queues.delivery import (
    DeliveryJob,
    DeliveryJobResult,
    DeliveryQueue,
)
from magi.new_bus.queues.llm import LLMJob, LLMJobQueue, LLMJobResult
from magi.new_bus.queues.tool import ToolJob, ToolJobQueue, ToolJobResult


__all__ = [
    "A2AInvocationJob",
    "A2AInvocationQueue",
    "A2AInvocationResult",
    "AgentRunJob",
    "AgentRunQueue",
    "AgentRunResult",
    "BaseJobQueue",
    "ChatJob",
    "ChatJobQueue",
    "ChatJobResult",
    "ConfigJob",
    "ConfigJobQueue",
    "ConfigJobResult",
    "DEFAULT_LEASE_SECONDS",
    "DeliveryJob",
    "DeliveryJobResult",
    "DeliveryQueue",
    "INLINE_PUBLISHER",
    "LLMJob",
    "LLMJobQueue",
    "LLMJobResult",
    "MAX_ATTEMPTS",
    "ToolJob",
    "ToolJobQueue",
    "ToolJobResult",
    "new_job_id",
]
