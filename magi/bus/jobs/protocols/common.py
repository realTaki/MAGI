"""Common DTOs used across bus services."""

from __future__ import annotations

from typing import Literal, TypeAlias

# ``AgentInbox.kind`` values; reused by the bus contracts and the
# consumer side (channel handlers, tool worker).
InboxKind: TypeAlias = Literal[
    "channel.message.received",
    "task.triggered",
    "tool.result",
    "tool.failed",
    "run.steer",
    "run.cancel",
    "a2a.request",
    "a2a.result",
]

# ``AgentRun.status`` values; reused by agent state machine + bus.
RunStatus: TypeAlias = Literal[
    "queued",
    "running",
    "waiting_tool",
    "waiting_a2a",
    "completed",
    "failed",
    "cancelled",
]
