"""Durable tool-job execution and result re-entry coverage."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi.bus import BusStore
from magi.bus.db import init_orm
from magi.tools.base import ToolResult


@pytest.mark.asyncio
async def test_tool_worker_returns_result_to_agent_inbox(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(state))
    init_orm(str(state / "memories"), seed_root=False)
    store = BusStore(str(state))
    store.enqueue_tool_job(
        run_id="run-1",
        tool_call_id="call-1",
        tool_name="fake_tool",
        arguments={"value": "hello"},
        context={"workspace": str(Path(tmp_path)), "uid": 1, "channel": "webui"},
    )

    from magi.tools import worker as worker_mod

    class FakeTool:
        async def run(self, _context, **kwargs):
            return ToolResult(content=f"done:{kwargs['value']}")

    monkeypatch.setattr(worker_mod, "get_tool", lambda *_args, **_kwargs: FakeTool())
    worker = worker_mod.ToolWorker(poll_seconds=0.01)
    await worker.start()
    try:
        for _ in range(50):
            claim = store.claim_next_agent_message("agent")
            if claim is not None:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("tool result was not published to agent inbox")
    finally:
        await worker.stop()

    assert claim.kind == "tool.result"
    assert claim.run_id == "run-1"
    assert claim.payload["text"] == "done:hello"
