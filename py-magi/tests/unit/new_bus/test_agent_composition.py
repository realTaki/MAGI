"""The runnable MAGI composition includes the durable Agent Worker."""

from __future__ import annotations

from agent.worker import AgentWorker
from channels.tasks import TaskWorker
from magi import WORKERS


def test_agent_worker_is_attached_by_the_runtime_composition() -> None:
    assert AgentWorker in WORKERS
    assert TaskWorker in WORKERS
