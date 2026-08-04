"""AgentWorker tests for durable asynchronous channels."""

from __future__ import annotations

import pytest

from magi.bus import AgentMessage
from magi.bus.db import init_orm


@pytest.fixture()
def worker_state(tmp_path, monkeypatch) -> str:
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    init_orm(str(state), seed_root=False)
    return str(state)


@pytest.mark.asyncio
async def test_worker_consumes_one_durable_turn(worker_state, monkeypatch) -> None:
    state = worker_state

    from magi.agent import step as step_mod
    from magi.agent.worker import (
        start_agent_worker,
        stop_agent_worker,
        submit_agent_message,
        wait_for_agent_run,
    )

    calls: list[dict] = []

    async def fake_step(_state_dir, **kwargs):
        calls.append(kwargs)
        return step_mod.AgentStepResult(
            text="durable reply",
            tool_uses=(),
            assistant_blocks=(),
            provider="test",
            model="test",
            usage={},
            messages=(),
        )

    monkeypatch.setattr(step_mod, "run_agent_step", fake_step)
    await start_agent_worker(state)
    try:
        run_id = await submit_agent_message(
            AgentMessage(
                event_id="worker-message-1",
                text="hello",
                channel="webui",
                session_id="session-1",
                uid=1,
            ),
            state_dir=state,
        )
        reply = await wait_for_agent_run(run_id, state_dir=state, timeout_seconds=2)
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
            "max_tokens": 1024,
            "continuation_messages": None,
            "tool_results": None,
        }
    ]


@pytest.mark.asyncio
async def test_worker_resumes_after_durable_tool_result(worker_state, monkeypatch) -> None:
    from magi.agent import step as step_mod
    from magi.agent.worker import (
        start_agent_worker,
        stop_agent_worker,
        submit_agent_message,
        wait_for_agent_run,
    )
    from magi.tools import worker as tool_worker_mod

    steps: list[dict] = []

    async def fake_step(_state_dir, **kwargs):
        steps.append(kwargs)
        if len(steps) == 1:
            return step_mod.AgentStepResult(
                text="",
                tool_uses=({"id": "call-1", "name": "fake_tool", "input": {}},),
                assistant_blocks=(),
                provider="test",
                model="test",
                usage={},
                messages=({"role": "user", "content": "hello", "content_blocks": None},),
            )
        return step_mod.AgentStepResult(
            text="after tool",
            tool_uses=(),
            assistant_blocks=(),
            provider="test",
            model="test",
            usage={},
            messages=(),
        )

    class FakeTool:
        async def run(self, _context, **_kwargs):
            from magi.tools.base import ToolResult

            return ToolResult(content="tool output")

    monkeypatch.setattr(step_mod, "run_agent_step", fake_step)
    monkeypatch.setattr(tool_worker_mod, "get_tool", lambda *_args, **_kwargs: FakeTool())
    await start_agent_worker(worker_state)
    tool_worker = tool_worker_mod.ToolWorker(worker_state, poll_seconds=0.01)
    await tool_worker.start()
    try:
        run_id = await submit_agent_message(
            AgentMessage(event_id="tool-run", text="hello", channel="webui", uid=1),
            state_dir=worker_state,
        )
        reply = await wait_for_agent_run(run_id, state_dir=worker_state, timeout_seconds=2)
    finally:
        await tool_worker.stop()
        await stop_agent_worker()

    assert reply == "after tool"
    assert len(steps) == 2
    assert steps[1]["tool_results"] == [
        {
            "type": "tool_result",
            "tool_use_id": "call-1",
            "content": "tool output",
            "is_error": False,
        }
    ]
