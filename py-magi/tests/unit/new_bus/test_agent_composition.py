"""The runnable MAGI composition includes the durable Agent Worker."""

from __future__ import annotations

from agent.worker import AgentWorker
from bus.magi import WORKERS


def test_agent_worker_is_attached_by_the_runtime_composition() -> None:
    assert AgentWorker in WORKERS
