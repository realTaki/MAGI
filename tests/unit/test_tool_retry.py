"""Coverage for the tool-job retry / dead-letter path (design §11.2).

A failed tool job moves to ``status='retry'`` until
``_MAX_TOOL_JOB_ATTEMPTS`` attempts have been recorded; past that,
it moves to ``status='dead'`` and the actor worker unblocks the
run with a synthetic ``tool.failed`` event so the run can resume
rather than hang in ``waiting_tool``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.models import AgentInbox, ToolCall, ToolJob
from magi.db import init_orm, open_session
from magi.db.base import utcnow_naive


@pytest.fixture()
def store(tmp_path, monkeypatch) -> BusStore:
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    init_orm(str(tmp_path), seed_root=False)
    return BusStore(str(tmp_path))


def _seed_run_with_tool(store: BusStore, tool_call_id: str) -> str:
    """Boot a run and ``wait_for_tools`` with the given call id."""
    run_id = store.publish_agent_message(AgentMessage(
        event_id=f"retry-root-{tool_call_id}",
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
            "tool_call_ids": [tool_call_id],
        },
        jobs=[{
            "tool_call_id": tool_call_id,
            "tool_name": "fake_tool",
            "arguments": {},
            "context": {},
        }],
    )
    return run_id


def _claim(store: BusStore, run_id: str) -> ToolJob:
    with open_session() as session:
        return session.query(ToolJob).filter(ToolJob.run_id == run_id).one()


def test_retry_tool_job_moves_to_retry_with_backoff(
    store: BusStore,
) -> None:
    """First failure stays in flight — the job goes to ``retry``."""
    run_id = _seed_run_with_tool(store, "call-a")
    # Simulate the lease has been taken once (ToolWorker increments
    # attempts via claim_next_tool_job) and the tool returned an
    # error. We don't run the worker — we manually increment to
    # keep the test deterministic.
    with open_session() as session:
        row = session.query(ToolJob).filter(ToolJob.run_id == run_id).one()
        row.attempts = 1
        row.status = "failed"
        session.commit()
        job_id = row.job_id

    before = utcnow_naive()
    store.retry_tool_job(job_id)
    after = utcnow_naive()

    with open_session() as session:
        row = session.query(ToolJob).filter(ToolJob.job_id == job_id).one()
    assert row.status == "retry"
    assert row.leased_by is None
    assert row.leased_until is None
    # Backoff for attempts=1 is 5s; available_at should land in the
    # window (before + 5s, after + 5s) — give a generous tolerance.
    assert before + timedelta(seconds=1) <= row.available_at <= after + timedelta(seconds=10)


def test_retry_tool_job_dead_letters_after_max_attempts(
    store: BusStore,
) -> None:
    """Past the cap, the job is dead-lettered and the actor unblocks."""
    run_id = _seed_run_with_tool(store, "call-dead")
    # Force the attempt counter up to the cap so a single retry
    # triggers the dead-letter branch.
    with open_session() as session:
        row = session.query(ToolJob).filter(ToolJob.run_id == run_id).one()
        row.attempts = store._MAX_TOOL_JOB_ATTEMPTS
        row.status = "failed"
        session.commit()

    store.retry_tool_job(row.job_id)

    with open_session() as session:
        dead = session.query(ToolJob).filter(ToolJob.job_id == row.job_id).one()
    assert dead.status == "dead"

    # The synthetic tool.failed AgentInbox event must exist so the
    # actor worker can claim it and resume.
    with open_session() as session:
        event = session.query(AgentInbox).filter(
            AgentInbox.event_id == f"tool-failed:{row.tool_call_id}"
        ).one()
    assert event.kind == "tool.failed"
    assert event.payload["dead_lettered"] is True
    assert event.payload["is_error"] is True

    # The ToolCall row is mirrored to ``failed`` so
    # ``load_tool_continuation`` sees the run's expected tool calls
    # all terminated.
    with open_session() as session:
        tool_call = session.query(ToolCall).filter(
            ToolCall.tool_call_id == row.tool_call_id
        ).one()
    assert tool_call.status == "failed"
    assert tool_call.result["is_error"] is True


def test_complete_tool_job_marks_failed_status(store: BusStore) -> None:
    """``complete_tool_job(..., is_error=True)`` flips status to failed.

    Without this distinction, ``retry_tool_job`` couldn't tell a
    transient error from a successful completion when it queries
    the row's status.
    """
    run_id = _seed_run_with_tool(store, "call-b")
    claim = store.claim_next_tool_job("tools-1")
    assert claim is not None
    store.complete_tool_job(claim, content="boom", is_error=True)

    with open_session() as session:
        row = session.query(ToolJob).filter(ToolJob.job_id == claim.job_id).one()
    assert row.status == "failed"


def test_complete_tool_job_marks_completed_status(store: BusStore) -> None:
    """Successful completion is ``completed`` (not ``failed``)."""
    run_id = _seed_run_with_tool(store, "call-c")
    claim = store.claim_next_tool_job("tools-1")
    assert claim is not None
    store.complete_tool_job(claim, content="ok", is_error=False)

    with open_session() as session:
        row = session.query(ToolJob).filter(ToolJob.job_id == claim.job_id).one()
    assert row.status == "completed"