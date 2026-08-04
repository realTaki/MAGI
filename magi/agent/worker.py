"""The single-turn consumer owned by :mod:`magi.agent`.

It serialises claims: one MAGI owns one active agent turn. Channels publish
durable inputs and never wait for the inference or a tool result.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from contextlib import suppress

from magi.bus import (
    A2AInvocationRequest,
    AgentMessage,
    BusClaim,
    BusStore,
    BusStoreProtocol,
    RunResult,
    StreamEvent,
    get_stream_hub,
)
from magi.constants import STATE_DIR

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

    def __init__(
        self,
        state_dir: str | None = None,
        *,
        poll_seconds: float = 0.25,
        store: BusStoreProtocol | None = None,
    ) -> None:
        self.state_dir = state_dir or __import__("os").environ.get("MAGI_STATE_DIR") or STATE_DIR
        self.store: BusStoreProtocol = store or BusStore(self.state_dir)
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
        from magi.agent.auto_title import start_title_worker
        await start_title_worker()
        self._task = asyncio.create_task(self._run(), name="magi-agent-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        from magi.agent.auto_title import stop_title_worker
        await stop_title_worker()

    def notify(self) -> None:
        """Wake the local poller after an in-process producer publishes."""
        self._wake.set()

    def _is_within_deadline(self, claim: BusClaim) -> bool:
        """Return True iff the run's deadline (if any) is still in the future.

        Reads ``agent_runs.deadline_at`` (set by
        :class:`AgentMessage.deadline_at` at publish time). A
        deadline in the past means the run is expired and should
        fail rather than invoke the LLM. A row with no deadline
        (None) is always considered within-deadline.
        """
        checker = getattr(self.store, "is_run_within_deadline", None)
        # Third-party protocol fakes written before the deadline API are
        # still safe: they have no persisted deadline to evaluate.
        return True if checker is None else bool(checker(claim.run_id))

    async def _run(self) -> None:
        while not self._stopping:
            expire_a2a = getattr(self.store, "expire_a2a_invocations", None)
            if expire_a2a is not None:
                expire_a2a()
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
        try:
            # Deadline gate (added 0011_agent_run_metadata): a run
            # whose ``deadline_at`` has passed is terminally failed
            # without invoking the LLM. The producer may pass a
            # deadline via AgentMessage.deadline_at; a scheduler tick
            # that schedules a long-running tool chain is the
            # canonical use case.
            if not self._is_within_deadline(claim):
                self.store.fail_agent_message(
                    claim.event_id,
                    error_code="magi.run_deadline_exceeded",
                    error_detail=(
                        f"run deadline exceeded before claim "
                        f"({claim.kind} for run {claim.run_id})"
                    ),
                )
                return
            if claim.kind == "run.steer":
                # Publication already atomically attached the input to its
                # active run.  This inbox record is only an acknowledgement;
                # it must not start another inference while tools are running.
                self.store.complete_agent_input(claim.event_id)
                return
            if claim.kind in {"tool.result", "a2a.result"}:
                resumed = self.store.load_tool_continuation(claim.run_id)
                if resumed is None:
                    self.store.complete_agent_input(claim.event_id)
                    return
                continuation, tool_results = resumed
                payload = dict(continuation["input"])
                steering_inputs = self.store.pending_steering_inputs(claim.run_id)
                await self._advance(
                    claim,
                    payload,
                    continuation_messages=list(continuation["messages"]),
                    tool_results=tool_results,
                    steering_inputs=steering_inputs or None,
                )
                return
            await self._advance(claim, claim.payload)
        except Exception as exc:  # a durable run must not strand its producer
            error_code = _error_code(exc)
            logger.exception("agent run %s failed", claim.run_id)
            self.store.fail_agent_message(
                claim.event_id, error_code=error_code, error_detail=str(exc)
            )
            return

    async def _advance(
        self,
        claim: BusClaim,
        payload: dict,
        *,
        continuation_messages: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        steering_inputs: list[dict] | None = None,
    ) -> None:
        from magi.agent.agent_context import DEFAULT_MAX_TOKENS
        from magi.agent.step import run_agent_step
        from magi.launcher.paths import workspace_root

        attempt_id = self.store.start_llm_attempt(claim.run_id, claim.event_id)
        hub = get_stream_hub()
        sequence = 1
        streamed_text = False
        hub.publish(StreamEvent(claim.run_id, attempt_id, sequence, "llm.started", {}))

        async def _forward_stream_event(event) -> None:
            nonlocal sequence, streamed_text
            sequence += 1
            if event.kind == "text.delta":
                streamed_text = True
            hub.publish(
                StreamEvent(
                    claim.run_id,
                    attempt_id,
                    sequence,
                    f"llm.{event.kind}",
                    dict(event.payload),
                )
            )

        kwargs = {
            "text": str(payload.get("text") or ""),
            "channel": str(payload.get("channel") or ""),
            "session_id": payload.get("session_id"),
            "uid": payload.get("uid"),
            "caller_role": payload.get("caller_role"),
            "max_tokens": DEFAULT_MAX_TOKENS,
            "continuation_messages": continuation_messages,
            "tool_results": tool_results,
        }
        # Keep the compatibility step call byte-for-byte stable unless there
        # is actual steering to append.  Older direct callers/tests therefore
        # remain valid while the new path gets provider-valid ordering.
        if steering_inputs:
            kwargs["steering_inputs"] = steering_inputs
        # Third-party/tests may still replace the compatibility step with the
        # old call signature.  Native runtime code opts into streaming without
        # making that migration surface a breaking change.
        if "on_stream_event" in inspect.signature(run_agent_step).parameters:
            kwargs["on_stream_event"] = _forward_stream_event
        try:
            step = await run_agent_step(self.state_dir, **kwargs)
        except Exception as exc:
            self.store.fail_llm_attempt(attempt_id, str(exc))
            hub.publish(StreamEvent(claim.run_id, attempt_id, sequence + 1, "llm.failed", {"error": str(exc)}))
            raise
        if step.text and not streamed_text:
            sequence += 1
            hub.publish(StreamEvent(claim.run_id, attempt_id, sequence, "llm.text.delta", {"text": step.text}))
        attempt_result = {
            "text": step.text,
            "assistant_blocks": list(step.assistant_blocks),
            "provider": step.provider,
            "model": step.model,
            "usage": step.usage,
        }
        if not step.tool_uses:
            self.store.commit_agent_transition(
                claim.event_id,
                reply=step.text,
                delivery_destination=_delivery_destination(self.state_dir, payload),
                continuation={"messages": list(step.messages), "assistant_blocks": list(step.assistant_blocks)},
                attempt_id=attempt_id,
                attempt_result=attempt_result,
            )
            self._enqueue_title_if_needed(payload)
            hub.publish(StreamEvent(claim.run_id, attempt_id, sequence + 1, "message.committed", {"text": step.text}))
            return
        context = {
            "workspace": str(workspace_root(self.state_dir)),
            "uid": payload.get("uid"),
            "channel": payload.get("channel"),
            "session_id": payload.get("session_id"),
            "caller_role": payload.get("caller_role"),
        }
        a2a_requests: list[A2AInvocationRequest] = []
        regular_tool_uses = []
        for tool_use in step.tool_uses:
            if tool_use.get("name") != "message_magi":
                regular_tool_uses.append(tool_use)
                continue
            arguments = dict(tool_use.get("input") or {})
            try:
                target_magic_id = int(arguments["magic_id"])
                text = str(arguments["text"])
                if target_magic_id <= 0 or not text.strip():
                    raise ValueError("magic_id and text are required")
            except (KeyError, TypeError, ValueError) as exc:
                # Preserve provider-valid transcript even for malformed model
                # output; the next step receives an ordinary failed result.
                regular_tool_uses.append({
                    "id": tool_use["id"],
                    "name": "message_magi",
                    "input": {"_validation_error": str(exc)},
                })
                continue
            a2a_requests.append(
                A2AInvocationRequest(
                    tool_call_id=str(tool_use["id"]),
                    target_magic_id=target_magic_id,
                    text=text,
                    expect_reply=bool(arguments.get("expect_reply", False)),
                )
            )
        tool_call_ids = [str(tool_use["id"]) for tool_use in step.tool_uses]
        continuation = {
            "input": payload,
            "messages": list(step.messages),
            "tool_call_ids": tool_call_ids,
            "assistant_blocks": list(step.assistant_blocks),
        }
        jobs = [
            {
                "tool_call_id": tool_use["id"],
                "tool_name": tool_use["name"],
                "arguments": dict(tool_use.get("input") or {}),
                "context": context,
            }
            for tool_use in regular_tool_uses
        ]
        self.store.commit_agent_transition(
            claim.event_id,
            continuation=continuation,
            jobs=jobs,
            a2a_requests=a2a_requests,
            attempt_id=attempt_id,
            attempt_result=attempt_result,
        )
        hub.publish(StreamEvent(claim.run_id, attempt_id, sequence + 1, "message.committed", {"tool_calls": tool_call_ids}))

    def _enqueue_title_if_needed(self, payload: dict) -> None:
        """Schedule title generation from the agent side after a committed turn."""
        uid, session_id = payload.get("uid"), payload.get("session_id")
        if not isinstance(uid, int) or not isinstance(session_id, str):
            return
        from magi.bus import get_bus
        session = get_bus().session.get(uid, session_id)
        if session is None or session.title is not None or len(session.messages) != 2:
            return
        from magi.agent.auto_title import enqueue_title_job
        asyncio.create_task(
            enqueue_title_job(session.delivery_address, session.session_id, uid),
            name=f"magi-title-{session.session_id}",
        )


def _delivery_destination(state_dir: str, payload: dict) -> str | None:
    """Resolve a TG session address without importing a Telegram client."""
    if payload.get("channel") != "tg" or not payload.get("session_id"):
        return None
    from magi.bus import get_bus

    session = get_bus().session.get(payload.get("uid"), payload["session_id"])
    return session.delivery_address if session is not None else None


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
    resolved_state_dir = state_dir or __import__("os").environ.get("MAGI_STATE_DIR") or STATE_DIR
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
    store = BusStore(state_dir or __import__("os").environ.get("MAGI_STATE_DIR") or STATE_DIR)
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
