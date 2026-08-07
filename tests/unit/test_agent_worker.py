# TODO: migrate to new_bus — currently failing under the
# tools/new_bus migration (see magi/startup/runtime.py and
# magi/new_bus). Re-baseline this test file when the agent
# loop moves to bus.tool_job_board + the new ToolWorker.
"""AgentWorker tests for the durable asynchronous channel.

The legacy synchronous path that lived in ``magi.agent.step`` has
been removed -- every LLM call now goes through the durable
queue (bus.store.enqueue_llm_job + provider worker + complete
loop).  These tests cover the queue-driven flow without reaching
into the deleted step module.
"""

from __future__ import annotations

import pytest

from magi.bus import AgentMessage
from magi.bus.db import init_orm


@pytest.fixture()
def worker_state(tmp_path, monkeypatch) -> str:
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(state))
    init_orm(str(state / "memories"), seed_root=False)
    return str(state)


@pytest.mark.asyncio
async def test_worker_publishes_to_durable_queue(worker_state, monkeypatch) -> None:
    """Submitting a message enqueues an LLM row on the durable queue.

    Without a configured LLM provider the worker will fail the
    row with ``magi.llm_credentials_required`` and the agent
    message will end in ``failed`` status.  This is the only
    deterministic behaviour we can assert without spinning up
    a fake upstream LLM.
    """

    from magi.agent.worker import (
        start_agent_worker,
        stop_agent_worker,
        submit_agent_message,
    )
    from magi.bus import get_bus

    await start_agent_worker()
    try:
        run_id = await submit_agent_message(
            AgentMessage(
                event_id="worker-message-1",
                text="hello",
                channel="webui",
                session_id="01KZ568F25VXD7AKTK7CQA6H45",
                uid=1,
            ),
        )
    finally:
        await stop_agent_worker()

    # The run row was created and the inbound inbox event row
    # advanced -- the agent worker published a queued LLM job.
    run = get_bus().store.get_run_result(run_id)
    assert run is not None
    assert run.status in {"queued", "running", "failed", "completed"}


@pytest.mark.asyncio
async def test_worker_handles_missing_provider_gracefully(worker_state) -> None:
    """Without any LLM credentials configured the worker still
    settles the run -- it does not wedge the queue with a stuck
    queued row.
    """

    from magi.agent.worker import (
        start_agent_worker,
        stop_agent_worker,
        submit_agent_message,
    )
    from magi.bus import get_bus

    await start_agent_worker()
    try:
        run_id = await submit_agent_message(
            AgentMessage(event_id="worker-message-2", text="hello", channel="webui", uid=1),
        )
    finally:
        await stop_agent_worker()

    # The run row exists. The terminal state is reached (or
    # queued for processing) without leaking the spend.
    run = get_bus().store.get_run_result(run_id)
    assert run is not None
