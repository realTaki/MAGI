"""The single-turn consumer owned by :mod:`magi.agent`.

This is the compatibility bridge from today's ``handle_message`` loop to the
event-driven runtime. It serialises claims: one MAGI owns one active agent
turn. Channels publish durable inputs and may temporarily wait for a result;
later phases can replace the continuous tool loop without changing producers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from magi.bus import AgentMessage, BusClaim, BusStore, RunResult
from magi.db import require_state_dir

logger = logging.getLogger("magi.agent.worker")


class AgentRunFailed(RuntimeError):
    """A completed agent run whose error should be surfaced to its producer."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        super().__init__(result.error_detail or result.error_code or "agent run failed")


class AgentRunTimedOut(TimeoutError):
    """The durable run remains queued/running past a caller timeout."""


class AgentWorker:
    """Sequential consumer of one MAGI's ``agent_inbox`` stream."""

    def __init__(self, state_dir: str | None = None, *, poll_seconds: float = 0.25) -> None:
        self.state_dir = state_dir or require_state_dir()
        self.store = BusStore(self.state_dir)
        self.worker_id = f"agent-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        # Bus operations are short SQLite transactions. Keeping them on the
        # owning loop avoids an executor/thread hand-off during application
        # startup while the engine is still settling after migrations.
        recovered = self.store.recover_expired_leases()
        if recovered:
            logger.warning("recovered %s expired agent inbox leases", recovered)
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="magi-agent-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def notify(self) -> None:
        """Wake the local poller after an in-process producer publishes."""
        self._wake.set()

    async def _run(self) -> None:
        while not self._stopping:
            claim = self.store.claim_next_agent_message(self.worker_id)
            if claim is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
                continue
            await self._process(claim)

    async def _process(self, claim: BusClaim) -> None:
        payload = claim.payload
        try:
            # Dynamic import preserves the existing test seam and keeps a
            # future pure AgentStep implementation local to ``magi.agent``.
            from magi.agent.loop import handle_message

            reply = await handle_message(
                self.state_dir,
                text=str(payload["text"]),
                channel=str(payload["channel"]),
                session_id=payload.get("session_id"),
                uid=payload.get("uid"),
                caller_role=payload.get("caller_role"),
            )
        except Exception as exc:  # a durable run must not strand its producer
            error_code = _error_code(exc)
            logger.exception("agent run %s failed", claim.run_id)
            self.store.fail_agent_message(
                claim.event_id, error_code=error_code, error_detail=str(exc)
            )
            return
        self.store.complete_agent_message(claim.event_id, reply)


def _error_code(exc: Exception) -> str:
    from magi.agent.llm import LLMNotConfiguredError

    if isinstance(exc, LLMNotConfiguredError):
        return "magi.llm_credentials_required"
    return "chat.agent_crashed"


_worker: AgentWorker | None = None


async def start_agent_worker(state_dir: str | None = None) -> AgentWorker:
    """Start the process-local worker after SQLite has been initialised."""
    global _worker
    if _worker is None:
        _worker = AgentWorker(state_dir)
        await _worker.start()
    return _worker


async def stop_agent_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


async def submit_agent_message(message: AgentMessage, *, state_dir: str | None = None) -> str:
    """Durably publish a turn from any async channel context."""
    resolved_state_dir = state_dir or require_state_dir()
    store = BusStore(resolved_state_dir)
    run_id = store.publish_agent_message(message)
    if _worker is not None and _worker.state_dir == resolved_state_dir:
        _worker.notify()
    return run_id


async def wait_for_agent_run(
    run_id: str,
    *,
    state_dir: str | None = None,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 0.1,
) -> str:
    """Wait for a durable run result without depending on the worker's loop."""
    store = BusStore(state_dir or require_state_dir())
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        result = store.get_run_result(run_id)
        if result is None:
            raise AgentRunFailed(
                RunResult(run_id=run_id, status="failed", error_code="bus.run_missing")
            )
        if result.status == "completed":
            return result.reply or ""
        if result.status in {"failed", "cancelled"}:
            raise AgentRunFailed(result)
        if asyncio.get_running_loop().time() >= deadline:
            raise AgentRunTimedOut(f"agent run {run_id} did not complete in time")
        await asyncio.sleep(poll_seconds)


async def submit_and_wait_agent_message(
    message: AgentMessage,
    *,
    state_dir: str | None = None,
    timeout_seconds: float = 180.0,
) -> str:
    """Compatibility helper for request/response channels during migration."""
    # Production owns the long-lived worker in the runtime FastAPI lifespan.
    # A bounded fallback keeps CLI/test callers safe without leaving a worker
    # task attached to an arbitrary temporary event loop.
    started_here = _worker is None
    if started_here:
        await start_agent_worker(state_dir)
    try:
        run_id = await submit_agent_message(message, state_dir=state_dir)
        return await wait_for_agent_run(
            run_id, state_dir=state_dir, timeout_seconds=timeout_seconds
        )
    finally:
        if started_here:
            await stop_agent_worker()
