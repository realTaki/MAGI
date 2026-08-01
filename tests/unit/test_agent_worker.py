"""Compatibility AgentWorker tests before channel callers migrate."""

from __future__ import annotations

import pytest

from magi.bus import AgentMessage
from magi.db import init_orm


@pytest.fixture()
def worker_state(tmp_path, monkeypatch) -> str:
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    init_orm(str(state), seed_root=False)
    return str(state)


@pytest.mark.asyncio
async def test_worker_consumes_one_durable_turn(worker_state, monkeypatch) -> None:
    state = worker_state

    from magi.agent import loop
    from magi.agent.worker import (
        start_agent_worker,
        stop_agent_worker,
        submit_and_wait_agent_message,
    )

    calls: list[dict] = []

    async def fake_handle_message(_state_dir, **kwargs):
        calls.append(kwargs)
        return "durable reply"

    monkeypatch.setattr(loop, "handle_message", fake_handle_message)
    await start_agent_worker(state)
    try:
        reply = await submit_and_wait_agent_message(
            AgentMessage(
                event_id="worker-message-1",
                text="hello",
                channel="webui",
                session_id="session-1",
                uid=1,
            ),
            state_dir=state,
            timeout_seconds=2,
        )
    finally:
        await stop_agent_worker()

    assert reply == "durable reply"
    assert calls == [
        {
            "text": "hello",
            "channel": "webui",
            "session_id": "session-1",
            "uid": 1,
            "caller_role": None,
        }
    ]
