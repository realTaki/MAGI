"""Unit tests for new_bus Queues.

Exercises the basic publish / claim / submit_result round-trip
plus the inline-publish path for ConfigJobQueue and DeliveryQueue.
"""

from __future__ import annotations

import pytest

from magi.new_bus.db import EngineFactory
from magi.new_bus.queues import (
    A2AInvocationJob,
    A2AInvocationQueue,
    AgentRunJob,
    AgentRunQueue,
    AgentRunResult,
    ConfigJob,
    ConfigJobQueue,
    DeliveryJob,
    DeliveryJobResult,
    DeliveryQueue,
    LLMJob,
    LLMJobQueue,
    LLMJobResult,
    ToolJob,
    ToolJobQueue,
)


@pytest.fixture
def factory():
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


# -- LLMJobQueue -----------------------------------------------------


def test_llm_queue_publish_claim_submit(factory):
    q = LLMJobQueue(factory)
    job = LLMJob(
        run_id="r1", inbox_event_id="e1", provider="openai",
        model="gpt-4", phase="started",
        request={"messages": [{"role": "user", "content": "hi"}]},
    )
    job_id = q.publish(job)
    assert job_id != ""

    claimed = q.claim(worker_id="w1")
    assert claimed is not None
    assert claimed.attempt_id == job_id
    assert claimed.run_id == "r1"

    q.submit_result(
        job_id=job_id,
        LLMJobResult(
            attempt_id=job_id, success=True, status="completed",
            response={"text": "hello"},
        ),
    )
    result = q.get_result(job_id=job_id)
    assert result.success
    assert result.response == {"text": "hello"}


def test_llm_queue_recover_expired(factory):
    q = LLMJobQueue(factory)
    job = LLMJob(run_id="r1", phase="started", request={})
    job_id = q.publish(job)
    q.claim(worker_id="w1")
    # 1 row in 'processing' state
    count = q.recover_expired_leases()
    # lease hasn't actually expired (set to now+60s); should be 0
    assert count == 0


# -- ConfigJobQueue — inline-publish path ------------------------------


def test_config_job_queue_inline_publish(factory):
    fired: list[dict] = []
    q = ConfigJobQueue(factory, rebuild_callback=lambda payload: fired.append(payload or {}))
    job = ConfigJob(kind="provider.config_changed", payload={"provider": "openai"})
    job_id = q.publish(job, inline=True)
    assert job_id > 0
    assert len(fired) == 1
    assert fired[0] == {"provider": "openai"}
    # row is in completed state
    assert q.get_result(job_id=str(job_id)) is not None
    assert q.get_result(job_id=str(job_id)).success


def test_config_job_queue_drain(factory):
    fired: list[dict] = []
    q = ConfigJobQueue(factory, rebuild_callback=lambda payload: fired.append(payload or {}))
    q.publish(ConfigJob(kind="provider.config_changed", payload={"x": 1}))
    q.publish(ConfigJob(kind="provider.config_changed", payload={"x": 2}))
    count = q.drain(worker_id="w1")
    assert count == 2
    assert len(fired) == 2


# -- AgentRunQueue ----------------------------------------------------


def test_agent_run_queue_publish_claim_submit(factory):
    q = AgentRunQueue(factory)
    job = AgentRunJob(
        run_id="r1", conversation_id="c1", kind="chat",
        payload={"text": "hello"},
    )
    event_id = q.publish(job)
    claimed = q.claim(worker_id="w1")
    assert claimed is not None
    assert claimed.event_id == event_id

    result_obj = AgentRunResult(
        event_id=event_id, success=True, status="completed",
        result={"reply": "hi"},
    )
    q.submit_result(job_id=event_id, result=result_obj)
    result = q.get_result(job_id=event_id)
    assert result.success


# -- ToolJobQueue -----------------------------------------------------


def test_tool_job_queue_publish_claim(factory):
    q = ToolJobQueue(factory)
    job = ToolJob(
        run_id="r1", tool_call_id="tc1", tool_name="echo",
        payload={"x": 1}, max_attempts=3,
    )
    job_id = q.publish(job)
    assert q.claim(worker_id="w1").job_id == job_id


# -- DeliveryQueue — inline-publish path ------------------------------


def test_delivery_queue_inline_publish(factory):
    received: list[tuple[str, str | None, dict]] = []
    q = DeliveryQueue(
        factory,
        dispatch_callback=lambda ch, dst, payload: received.append((ch, dst, payload)),
    )
    job = DeliveryJob(
        channel="tg", destination="12345",
        payload={"text": "hi"},
    )
    delivery_id = q.publish(job, inline=True)
    assert len(received) == 1
    assert received[0] == ("tg", "12345", {"text": "hi"})
    result = q.get_result(job_id=delivery_id)
    assert result.success


def test_delivery_queue_no_dispatch_no_inline(factory):
    """Without a dispatch callback, inline-publish still succeeds but does nothing."""
    q = DeliveryQueue(factory)
    job = DeliveryJob(channel="tg", destination="12345", payload={"text": "hi"})
    delivery_id = q.publish(job, inline=True)
    result = q.get_result(job_id=delivery_id)
    assert result.success


# -- A2AInvocationQueue -----------------------------------------------


def test_a2a_invocation_queue_publish(factory):
    q = A2AInvocationQueue(factory)
    job = A2AInvocationJob(
        run_id="r1", target="peer-magi", request={"action": "ping"},
    )
    invocation_id = q.publish(job)
    assert invocation_id != ""
    claimed = q.claim(worker_id="w1")
    assert claimed is not None
    assert claimed.invocation_id == invocation_id
