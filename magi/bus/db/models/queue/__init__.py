"""ORM tables owned by the durable message bus (queue + run state).

These nine tables form the operational recovery surface for one MAGI's
private SQLite database. They are NOT user data — a node must be able to
recover a partially-processed turn after its own process restarts, and
these rows are the durable record that makes that possible.

Each table lives in its own submodule; this ``__init__`` re-exports for
internal BUS imports; no model is a public BUS contract.
"""

from magi.bus.db.models.queue.a2a_invocation import A2AInvocation
from magi.bus.db.models.queue.agent_inbox import AgentInbox
from magi.bus.db.models.queue.agent_run import AgentRun
from magi.bus.db.models.queue.control_job import ControlJob
from magi.bus.db.models.queue.delivery_outbox import DeliveryOutbox
from magi.bus.db.models.queue.llm_attempt import LLMAttempt
from magi.bus.db.models.queue.run_input import RunInput
from magi.bus.db.models.queue.tool_call import ToolCall
from magi.bus.db.models.queue.tool_job import ToolJob


__all__ = [
    "A2AInvocation",
    "AgentInbox",
    "AgentRun",
    "ControlJob",
    "DeliveryOutbox",
    "LLMAttempt",
    "RunInput",
    "ToolCall",
    "ToolJob",
]
