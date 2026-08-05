"""The single-turn consumer owned by :mod:`magi.agent`.

It serialises claims: one MAGI owns one active agent turn. Channels publish
durable inputs and never wait for the inference or a tool result.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from magi.bus import (
    A2AInvocationRequest,
    AgentMessage,
    BusClaim,
    BusStoreProtocol,
    RunResult,
    StreamEvent,
    get_bus_store,
    get_stream_hub,
)
from magi.launcher.paths import workspace_dir

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
        *,
        poll_seconds: float = 0.25,
        store: BusStoreProtocol | None = None,
    ) -> None:
        self.store: BusStoreProtocol = store or get_bus_store()
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
        # Phase D — ``start_title_worker`` no longer exists; title
        # jobs route through the providers queue and are consumed
        # by :class:`ProvidersWorker`.
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

    def _is_within_deadline(self, claim: BusClaim) -> bool:
        """Return True iff the run's deadline (if any) is still in the future."""
        checker = getattr(self.store, "is_run_within_deadline", None)
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
                # Phase D: a steering input that's also a real turn
                # builder still goes through the queue. We reuse the
                # same ``_enqueue_llm`` helper and pass the payload as
                # the input — which (today) is the channel.message payload.
                self.store.complete_agent_input(claim.event_id)
                return
            if claim.kind == "provider.completed":
                await self._apply_provider_result(claim)
                return
            if claim.kind in {"tool.result", "a2a.result"}:
                resumed = self.store.load_tool_continuation(claim.run_id)
                if resumed is None:
                    self.store.complete_agent_input(claim.event_id)
                    return
                continuation, tool_results = resumed
                payload = dict(continuation["input"])
                steering_inputs = self.store.pending_steering_inputs(claim.run_id)
                await self._enqueue_llm(
                    claim,
                    payload,
                    continuation_messages=list(continuation["messages"]),
                    tool_results=tool_results,
                    steering_inputs=steering_inputs or None,
                )
                return
            await self._enqueue_llm(claim, claim.payload)
        except Exception as exc:
            error_code = _error_code(exc)
            logger.exception("agent run %s failed", claim.run_id)
            self.store.fail_agent_message(
                claim.event_id, error_code=error_code, error_detail=str(exc)
            )
            return

    async def _enqueue_llm(
        self,
        claim: BusClaim,
        payload: dict,
        *,
        continuation_messages: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        steering_inputs: list[dict] | None = None,
    ) -> None:
        """Build the request, publish onto the providers queue, return.

        Phase D — the actual LLM call now runs in
        :class:`ProvidersWorker` (sibling task). The provider worker
        publishes a ``provider.completed`` ``AgentInbox`` row with
        ``metadata.attempt_id`` once the attempt settles; the agent
        worker picks that up via :meth:`_process` →
        :meth:`_apply_provider_result`.

        Side-effects:
        - Opens an ``LLMAttempt`` row via ``start_llm_attempt``
          (the durable lifecycle row the worker reads).
        - Publishes a queued :class:`ProviderJob` so the provider
          worker can claim it.
        - ``complete_agent_input`` advances the inbox row so a
          subsequent ``provider.completed`` event is the one that
          re-wakes the agent loop.
        """
        from magi.agent._step_helpers import (
            assemble_agent_request, fallback_agent_result,
        )
        from magi.bus.protocols.provider_jobs import ProviderJob
        from magi.providers.worker import enqueue_provider_job

        attempt_id = self.store.start_llm_attempt(claim.run_id, claim.event_id)

        built = await assemble_agent_request(
            text=str(payload.get("text") or ""),
            channel=str(payload.get("channel") or ""),
            uid=payload.get("uid"),
            session_id=payload.get("session_id"),
            caller_role=payload.get("caller_role"),
            continuation_messages=continuation_messages,
            tool_results=tool_results,
            steering_inputs=steering_inputs,
        )

        if built is None:
            # Pre-bake a canned "fallback" row so the apply path can
            # run on the standard terminal-result flow without a
            # provider call. This matches today's behaviour for the
            # no-credentials / no-context cases.
            fallback = fallback_agent_result(
                "agent_no_credentials" if not payload.get("uid") else "agent_fallback"
            )
            self.store.complete_llm_attempt(
                attempt_id,
                response={
                    "text": fallback["text"],
                    "thinking": None,
                    "tool_uses": [],
                    "raw_blocks": [],
                    "model": None,
                    "usage": {},
                    "stop_reason": "fallback",
                },
            )
            self.store.complete_agent_input(claim.event_id)
            # Synthesize a provider.completed event so the agent loop
            # finishes the run through the same path as a real call.
            synth = AgentMessage(
                event_id=f"provider-fallback:{attempt_id}",
                text="",
                channel="agent.internal",
                session_id=None,
                uid=None,
                kind="provider.completed",
                target_run_id=claim.run_id,
                metadata={
                    "attempt_id": attempt_id,
                    "status": "completed",
                    "error_code": None,
                    "error_detail": None,
                },
            )
            self.store.publish_agent_message(synth)
            return

        system, messages, tools, max_tokens = built
        # The agent worker needs the original input back when the
        # ``provider.completed`` event arrives. We stash the slim
        # subset it actually uses (uid, session_id, channel,
        # caller_role, text) on the job's ``extra`` payload so the
        # worker doesn't have to re-derive them from the run row.
        extra = {
            "uid": payload.get("uid"),
            "session_id": payload.get("session_id"),
            "channel": payload.get("channel"),
            "caller_role": payload.get("caller_role"),
            "text": str(payload.get("text") or ""),
        }
        job = ProviderJob(
            attempt_id="",  # assigned by enqueue_provider_job
            run_id=claim.run_id,
            inbox_event_id=claim.event_id,
            kind="agent.step",
            system=system,
            messages=tuple(messages),
            max_tokens=max_tokens,
            tools=tuple(tools) if tools else None,
            streaming=False,
            extra=extra,
        )
        await enqueue_provider_job(job)
        self.store.complete_agent_input(claim.event_id)

    async def _apply_provider_result(self, claim: BusClaim) -> None:
        """Apply a ``provider.completed`` inbox event — finish the turn.

        Loads the durable ``LLMAttempt.response`` row, then runs the
        same post-step branches the old synchronous ``_advance``
        ran after ``step = await run_agent_step(...)`` returned.
        """
        metadata = (claim.payload or {}).get("metadata") or {}
        attempt_id = metadata.get("attempt_id")
        status = metadata.get("status")
        error_code = metadata.get("error_code")
        error_detail = metadata.get("error_detail")
        if not attempt_id:
            # Defensive: malformed event, drop it.
            self.store.complete_agent_input(claim.event_id)
            return
        if status == "failed":
            self.store.fail_llm_attempt(
                attempt_id, error_detail or "provider call failed",
            )
            self.store.fail_agent_message(
                claim.event_id,
                error_code=error_code or "chat.provider_crashed",
                error_detail=error_detail or "",
            )
            return
        # status == "completed" — load the persisted result row.
        result = self.store.load_provider_job_result(
            attempt_id, wait_seconds=5, poll_seconds=0.05,
        )
        if result is None or result["status"] != "completed":
            # Defensive: provider.completed fired but the row's not
            # there. Try again with a longer wait once.
            if result is None:
                result = self.store.load_provider_job_result(
                    attempt_id, wait_seconds=10, poll_seconds=0.1,
                )
            if result is None or result["status"] != "completed":
                self.store.fail_agent_message(
                    claim.event_id,
                    error_code="chat.provider_crashed",
                    error_detail=(
                        f"provider.completed for {attempt_id} had no terminal row"
                    ),
                )
                return

        response = result["response"]
        step_text = response.get("text") or ""
        step_tool_uses = response.get("tool_uses") or []
        step_assistant_blocks = response.get("raw_blocks") or []
        step_provider_name = response.get("provider") or ""
        step_model = response.get("model")
        step_usage = dict(response.get("usage") or {})

        # Reconstruct ``messages`` (the assistant transcript). We
        # don't have the in-memory message list at this point, but
        # ``provider.events`` typed us the deltas — the persisted
        # ``raw_blocks`` is the assistant turn verbatim.
        step_messages = []
        attempt_request = self.store.load_provider_job_request(attempt_id)
        original_payload = {}
        if attempt_request is not None:
            for m in attempt_request.get("messages") or []:
                step_messages.append({
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "content_blocks": m.get("content_blocks"),
                })
            original_payload = dict(attempt_request.get("extra") or {})
        step_messages.append({
            "role": "assistant",
            "content": step_text,
            "content_blocks": step_assistant_blocks or None,
        })

        attempt_result = {
            "text": step_text,
            "assistant_blocks": list(step_assistant_blocks),
            "provider": step_provider_name,
            "model": step_model,
            "usage": step_usage,
        }
        if not step_tool_uses:
            self.store.commit_agent_transition(
                claim.event_id,
                reply=step_text,
                delivery_destination=_delivery_destination(original_payload),
                continuation={
                    "messages": step_messages,
                    "assistant_blocks": list(step_assistant_blocks),
                },
                attempt_id=attempt_id,
                attempt_result=attempt_result,
            )
            self._enqueue_title_if_needed(original_payload)
            hub = get_stream_hub()
            hub.publish(
                StreamEvent(
                    claim.run_id, attempt_id, 0,
                    "message.committed", {"text": step_text},
                )
            )
            return
        context = {
            "workspace": str(workspace_dir()),
            "uid": original_payload.get("uid"),
            "channel": original_payload.get("channel"),
            "session_id": original_payload.get("session_id"),
            "caller_role": original_payload.get("caller_role"),
        }
        a2a_requests: list[A2AInvocationRequest] = []
        regular_tool_uses = []
        for tool_use in step_tool_uses:
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
        tool_call_ids = [str(tool_use["id"]) for tool_use in step_tool_uses]
        continuation = {
            "input": original_payload,
            "messages": step_messages,
            "tool_call_ids": tool_call_ids,
            "assistant_blocks": list(step_assistant_blocks),
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
        hub = get_stream_hub()
        hub.publish(
            StreamEvent(
                claim.run_id, attempt_id, 0,
                "message.committed", {"tool_calls": tool_call_ids},
            )
        )

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


def _delivery_destination(payload: dict) -> str | None:
    """Resolve a TG session address without importing a Telegram client."""
    if payload.get("channel") != "tg" or not payload.get("session_id"):
        return None
    from magi.bus import get_bus

    session = get_bus().session.get(payload.get("uid"), payload["session_id"])
    return session.delivery_address if session is not None else None


def _error_code(exc: Exception) -> str:
    from magi.providers import LLMNotConfiguredError

    if isinstance(exc, LLMNotConfiguredError):
        return "magi.llm_credentials_required"
    return "chat.agent_crashed"


_worker: AgentWorker | None = None


async def start_agent_worker() -> AgentWorker:
    """Start the process-local worker after SQLite has been initialised."""
    global _worker
    if _worker is None:
        _worker = AgentWorker()
        await _worker.start()
    return _worker


async def stop_agent_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


async def submit_agent_message(message: AgentMessage) -> str:
    """Durably publish a turn from any async channel context."""
    store = get_bus_store()
    run_id = store.publish_agent_message(message)
    if _worker is not None:
        _worker.notify()
    return run_id


async def wait_for_agent_run(
    run_id: str,
    *,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 0.1,
) -> str:
    """Wait for a durable run result without depending on the worker's loop."""
    store = get_bus_store()
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
