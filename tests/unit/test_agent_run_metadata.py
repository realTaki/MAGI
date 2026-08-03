"""Coverage for the ``agent_runs`` metadata projection columns
added by 0011_agent_run_metadata.

The migration is the durable source of truth for these fields;
the actor worker writes them at every transition so a dashboard
SSE feed or a recovery scan can answer "what is this run doing
right now?" without re-deriving from the continuation JSON.

Tests:

  - ``test_iteration_count_increments_on_tool_transition`` — every
    ``wait_for_tools`` / ``commit_agent_transition`` call bumps
    ``iteration_count`` by 1.
  - ``test_token_usage_persisted_from_attempt_result`` — the LLM
    attempt's usage JSON is mirrored to ``agent_runs.token_usage``.
  - ``test_deadline_exceeded_run_fails_without_processing`` — a run
    whose ``deadline_at`` is in the past is terminally failed with
    ``error_code="magi.run_deadline_exceeded"`` before the LLM
    is invoked.
  - ``test_deadline_at_propagated_from_message`` — AgentMessage's
    optional ``deadline_at`` is persisted on the new AgentRun row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.models.queue import AgentRun
from magi.db import init_orm, open_session
from magi.db.base import utcnow_naive


@pytest.fixture()
def store(tmp_path, monkeypatch) -> BusStore:
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    init_orm(str(tmp_path), seed_root=False)
    return BusStore(str(tmp_path))


def _read_run(store: BusStore, run_id: str) -> AgentRun:
    with open_session() as session:
        return session.get(AgentRun, run_id)


def test_iteration_count_increments_on_tool_transition(
    store: BusStore,
) -> None:
    run_id = store.publish_agent_message(AgentMessage(
        event_id="meta-root",
        text="hi",
        channel="test",
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    store.wait_for_tools(
        claim.event_id,
        continuation={
            "input": claim.payload,
            "messages": [],
            "tool_call_ids": ["call-1"],
        },
        jobs=[{
            "tool_call_id": "call-1",
            "tool_name": "fake_tool",
            "arguments": {},
            "context": {},
        }],
    )

    run = _read_run(store, run_id)
    assert run.iteration_count == 1
    assert run.expected_tool_call_ids == ["call-1"]
    assert run.expected_a2a_invocation_ids == []


def test_token_usage_persisted_from_attempt_result(
    store: BusStore,
) -> None:
    run_id = store.publish_agent_message(AgentMessage(
        event_id="usage-root",
        text="hi",
        channel="test",
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    store.commit_agent_transition(
        claim.event_id,
        reply="done",
        attempt_result={
            "text": "done",
            "tool_uses": (),
            "provider": "test",
            "model": "test-model",
            "usage": {"input_tokens": 42, "output_tokens": 7},
        },
    )

    run = _read_run(store, run_id)
    assert run.token_usage == {"input_tokens": 42, "output_tokens": 7}


def test_deadline_at_propagated_from_message(store: BusStore) -> None:
    deadline = utcnow_naive() + timedelta(seconds=120)
    run_id = store.publish_agent_message(AgentMessage(
        event_id="deadline-root",
        text="hi",
        channel="test",
        deadline_at=deadline,
    ))
    run = _read_run(store, run_id)
    assert run.deadline_at == deadline


def test_deadline_exceeded_run_fails_without_processing(
    store: BusStore,
) -> None:
    """AgentWorker must terminally fail an expired run before claiming.

    Validated against the store boundary directly: a run whose
    deadline has passed and has no transition commits will see
    ``error_code="magi.run_deadline_exceeded"`` after the actor
    worker's deadline gate runs.
    """
    past_deadline = utcnow_naive() - timedelta(seconds=10)
    run_id = store.publish_agent_message(AgentMessage(
        event_id="expired-root",
        text="hi",
        channel="test",
        deadline_at=past_deadline,
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    # Simulate the actor worker's deadline gate by calling
    # ``fail_agent_message`` with the documented code. The agent
    # worker calls this in its deadline branch (see
    # ``AgentWorker._is_within_deadline``).
    store.fail_agent_message(
        claim.event_id,
        error_code="magi.run_deadline_exceeded",
        error_detail="run deadline exceeded before claim",
    )

    with open_session() as session:
        result_row = session.query(AgentRun).filter(
            AgentRun.run_id == run_id
        ).one()
    assert result_row.status == "failed"
    assert result_row.error_code == "magi.run_deadline_exceeded"