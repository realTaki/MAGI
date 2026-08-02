"""Regression coverage for the private durable message bus."""

from __future__ import annotations

from datetime import timedelta

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.models import AgentInbox
from magi.db import init_orm, open_session
from magi.db.base import utcnow_naive


@pytest.fixture()
def bus_store(tmp_path, monkeypatch) -> BusStore:
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    init_orm(str(state), seed_root=False)
    return BusStore(str(state))


def _message(event_id: str = "webui-message-1") -> AgentMessage:
    return AgentMessage(
        event_id=event_id,
        text="hello",
        channel="webui",
        session_id="session-1",
        uid=7,
        caller_role="admin",
    )


def test_publish_is_idempotent_and_claims_fifo(bus_store: BusStore) -> None:
    first = bus_store.publish_agent_message(_message("one"))
    assert bus_store.publish_agent_message(_message("one")) == first
    second = bus_store.publish_agent_message(_message("two"))

    claim_one = bus_store.claim_next_agent_message("agent-worker")
    assert claim_one is not None
    assert claim_one.run_id == first
    assert claim_one.payload["text"] == "hello"

    bus_store.complete_agent_message(claim_one.event_id, "first reply")
    assert bus_store.get_run_result(first).reply == "first reply"  # type: ignore[union-attr]

    claim_two = bus_store.claim_next_agent_message("agent-worker")
    assert claim_two is not None
    assert claim_two.run_id == second


def test_expired_lease_is_recovered_after_worker_crash(bus_store: BusStore) -> None:
    run_id = bus_store.publish_agent_message(_message())
    claim = bus_store.claim_next_agent_message("dead-worker", lease_seconds=3600)
    assert claim is not None

    with open_session() as session:
        row = session.query(AgentInbox).filter_by(event_id=claim.event_id).one()
        row.leased_until = utcnow_naive() - timedelta(seconds=1)
        session.commit()

    assert bus_store.recover_expired_leases() == 1
    recovered = bus_store.claim_next_agent_message("replacement-worker")
    assert recovered is not None
    assert recovered.run_id == run_id
    assert recovered.attempts == 2


def test_failure_is_visible_to_a_waiting_producer(bus_store: BusStore) -> None:
    run_id = bus_store.publish_agent_message(_message())
    claim = bus_store.claim_next_agent_message("agent-worker")
    assert claim is not None

    bus_store.fail_agent_message(
        claim.event_id,
        error_code="magi.llm_credentials_required",
        error_detail="provider is not configured",
    )
    result = bus_store.get_run_result(run_id)
    assert result is not None
    assert result.status == "failed"
    assert result.error_code == "magi.llm_credentials_required"


def test_same_conversation_message_is_durable_steering_input(bus_store: BusStore) -> None:
    run_id = bus_store.publish_agent_message(_message("root"))
    root = bus_store.claim_next_agent_message("agent-worker")
    assert root is not None
    bus_store.wait_for_tools(
        root.event_id,
        continuation={"input": root.payload, "messages": [], "tool_call_ids": ["call-1"]},
        jobs=[
            {
                "tool_call_id": "call-1",
                "tool_name": "fake_tool",
                "arguments": {},
                "context": {},
            }
        ],
    )

    steered_run = bus_store.publish_agent_message(
        AgentMessage(
            event_id="steer-1",
            text="change the goal",
            channel="webui",
            session_id="session-1",
            uid=7,
        )
    )
    assert steered_run == run_id
    assert [row["text"] for row in bus_store.pending_steering_inputs(run_id)] == ["change the goal"]

    # A different conversation cannot run in parallel with this actor.
    bus_store.publish_agent_message(
        AgentMessage(event_id="other", text="later", channel="webui", session_id="session-2", uid=7)
    )
    steer_claim = bus_store.claim_next_agent_message("agent-worker")
    assert steer_claim is not None
    assert steer_claim.kind == "run.steer"
    assert steer_claim.run_id == run_id
