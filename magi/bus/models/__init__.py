"""ORM models owned by the BUS.

Only repositories and BUS services import these classes.  Domain packages
receive DTOs/contracts and never see SQLAlchemy rows.
"""

from magi.bus.models.queue import (
    A2AInvocation,
    AgentInbox,
    AgentRun,
    DeliveryOutbox,
    LLMAttempt,
    RunInput,
    ToolCall,
    ToolJob,
)

__all__ = [
    "A2AInvocation", "AgentInbox", "AgentRun", "DeliveryOutbox",
    "LLMAttempt", "RunInput", "ToolCall", "ToolJob",
]
