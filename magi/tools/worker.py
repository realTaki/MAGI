"""Durable tool-effect consumer owned by :mod:`magi.tools`.

The worker never imports channel implementations or the agent loop.  It
claims a persisted job, performs the external/local effect outside a database
transaction, then emits a durable ``tool.result`` inbox event for the agent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from pathlib import Path

from magi.bus import BusStore, ToolClaim
from magi.db import require_state_dir
from magi.tools.base import ToolContext
from magi.tools.registry import get_tool

logger = logging.getLogger("magi.tools.worker")


class ToolWorker:
    """Single durable tool consumer for one MAGI process."""

    def __init__(self, state_dir: str | None = None, *, poll_seconds: float = 0.25) -> None:
        self.state_dir = state_dir or require_state_dir()
        self.store = BusStore(self.state_dir)
        self.worker_id = f"tools-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="magi-tool-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            claim = self.store.claim_next_tool_job(self.worker_id)
            if claim is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._execute(claim)

    async def _execute(self, claim: ToolClaim) -> None:
        context_data = dict(claim.payload.get("context") or {})
        tool = get_tool(claim.tool_name, caller_role=context_data.get("caller_role"))
        if tool is None:
            self.store.complete_tool_job(
                claim, content=f"unknown or unauthorized tool: {claim.tool_name!r}", is_error=True
            )
            return
        try:
            result = await tool.run(
                ToolContext(
                    state_dir=self.state_dir,
                    workspace=Path(str(context_data["workspace"])),
                    uid=int(context_data.get("uid") or 0),
                    channel=str(context_data.get("channel") or ""),
                    session_id=str(context_data.get("session_id") or ""),
                ),
                **dict(claim.payload.get("arguments") or {}),
            )
            content = result.content[:8000]
            is_error = result.is_error
        except Exception as exc:  # tools report errors back to the actor
            logger.exception("tool job %s failed", claim.job_id)
            content = f"tool {claim.tool_name!r} crashed: {exc}"[:8000]
            is_error = True
        self.store.complete_tool_job(claim, content=content, is_error=is_error)


_worker: ToolWorker | None = None


async def start_tool_worker(state_dir: str | None = None) -> ToolWorker:
    global _worker
    if _worker is None:
        _worker = ToolWorker(state_dir)
        await _worker.start()
    return _worker


async def stop_tool_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None
