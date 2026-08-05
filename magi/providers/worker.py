"""Durable provider consumer owned by :mod:`magi.providers`.

The :class:`ProvidersWorker` is the **single** place every LLM
API call in MAGI goes through. It owns:

- the durable queue (``llm_attempts`` rows with ``status="queued"``
  the agent loop writes via :func:`enqueue_provider_job`);
- the streaming deltas (``StreamHub`` publishes keyed by ``run_id``
  so existing WebSocket / SSE consumers don't need to know about
  the worker);
- the plugin hooks (``Hook.BEFORE_LLM_CALL`` /
  ``Hook.AFTER_LLM_CALL``) — the only emit site that drives them.

Design principle
================

The worker is **a dumb LLM invoker**. It does not know whether the
queued job came from an agent turn, chat-history compaction, or a
session-title job. The origin caller reads the result off the
``LLMAttempt.response`` row (or via the ``provider.completed`` inbox
event) and does its own post-processing in its own layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import suppress
from typing import Any

# ``magi.bus.bootstrap`` and ``magi.bus.store`` are *deferred*
# to runtime (inside the methods that need them). Eager import
# would transitively register ``magi.bus.models.local.hook_evaluation``
# which carries a SQLAlchemy ``Mapped`` named ``metadata`` — that
# collides with the reserved ``Base.metadata`` on SQLAlchemy's
# declarative API. The double-registration trips the error any
# time ``magi.bus.bootstrap`` is statically imported after the
# app's own bootstrap has already registered the model. Touching
# only the leaf modules here keeps the worker module load cheap
# and lets the runtime's first ``bootstrap()`` call register each
# model exactly once.
from magi.bus.protocols.agent import AgentMessage, BusStoreProtocol, InboxKind
from magi.bus.protocols.provider_jobs import ProviderJob
from magi.bus.stream import StreamEvent
from magi.providers import get_provider
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.providers.provider import ChatMessage, ChatResult, LLMProvider

logger = logging.getLogger("magi.providers.worker")

# Backpressure cap. Two parallel upstream calls keep latency low
# while leaving room for a streaming job to take a long time
# without starving shorter turns. Override via ``MAGI_PROVIDER_CONCURRENCY``.
_DEFAULT_CONCURRENCY = 2

# Socket-side run deadline check: if the run's deadline already
# passed before the worker started the call, fail-fast without
# paying the upstream latency.
_DEADLINE_ERROR_CODE = "magi.run_deadline_exceeded"

# Stable short codes for the operator-facing error envelope.
_NOT_CONFIGURED_CODE = "magi.llm_credentials_required"
_PROVIDER_CRASHED_CODE = "chat.provider_crashed"


class ProvidersWorker:
    """Consumer that owns every LLM API call in a MAGI process."""

    def __init__(
        self,
        *,
        store: BusStoreProtocol | None = None,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        # Lazy-resolve the store so the module load doesn't trigger
        # the bus.bootstrap double-import chain (see top-of-file
        # note). Tests injecting a store skip this entirely.
        self.store = store or _lazy_get_bus_store()
        self.worker_id = f"provider-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        if concurrency is None:
            try:
                concurrency = int(
                    os.environ.get("MAGI_PROVIDER_CONCURRENCY", _DEFAULT_CONCURRENCY),
                )
            except ValueError:
                concurrency = _DEFAULT_CONCURRENCY
        self.concurrency = max(1, concurrency)
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._wake = asyncio.Event()
        self._slots = asyncio.Semaphore(self.concurrency)
        self._inflight: set[asyncio.Task[None]] = set()

    # ----- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        recovered = self.store.recover_expired_provider_leases()
        if recovered:
            logger.warning(
                "providers worker: recovered %d expired leases at boot",
                recovered,
            )
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="magi-provider-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Drain in-flight provider calls so the process exits cleanly.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()

    def notify(self) -> None:
        """Wake the poller after an in-process publish."""
        self._wake.set()

    # ----- main loop ----------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            claim = self.store.claim_next_provider_job(self.worker_id)
            if claim is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self.poll_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            attempt_id, run_id, inbox_event_id = claim
            await self._slots.acquire()
            task = asyncio.create_task(
                self._invoke_safe(attempt_id, run_id, inbox_event_id),
                name=f"provider-job-{attempt_id}",
            )
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _invoke_safe(
        self,
        attempt_id: str,
        run_id: str,
        inbox_event_id: str | None,
    ) -> None:
        try:
            await self._invoke_provider(attempt_id, run_id, inbox_event_id)
        except asyncio.CancelledError:
            self.store.complete_llm_attempt(
                attempt_id,
                error={
                    "detail": "providers worker cancelled",
                    "code": "magi.run_cancelled",
                },
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code="magi.run_cancelled",
                error_detail="providers worker cancelled",
            )
            raise
        except Exception as exc:  # noqa: BLE001 — worker MUST NOT crash the bus
            logger.exception(
                "providers worker: unhandled exception on attempt %s",
                attempt_id,
            )
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": str(exc), "code": _PROVIDER_CRASHED_CODE},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=_PROVIDER_CRASHED_CODE,
                error_detail=str(exc),
            )
        finally:
            self._slots.release()

    # ----- the single LLM call site ------------------------------------

    async def _invoke_provider(
        self,
        attempt_id: str,
        run_id: str,
        inbox_event_id: str | None,
    ) -> None:
        # 1. Re-check deadline — fail-fast if the run already expired
        # while the row sat in the queue.
        if not self.store.is_run_within_deadline(run_id):
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": "run deadline exceeded before provider call",
                       "code": _DEADLINE_ERROR_CODE},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=_DEADLINE_ERROR_CODE,
                error_detail="run deadline exceeded before provider call",
            )
            return

        # 2. Load the serialized request the caller enqueued.
        request = self.store.load_provider_job_request(attempt_id)
        if request is None:
            self.store.complete_llm_attempt(
                attempt_id,
                error={
                    "detail": "provider job had no serialized request",
                    "code": _PROVIDER_CRASHED_CODE,
                },
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=_PROVIDER_CRASHED_CODE,
                error_detail="provider job had no serialized request",
            )
            return

        # 3. Resolve provider (factory reads credentials from the
        # seeded adam Magi row via BUS). The hook emit before this
        # point would see ``provider=None``; emit AFTER so observers
        # know whether the call was actually attempted.
        try:
            provider = get_provider(model=request.get("model"))
        except LLMNotConfiguredError as exc:
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": str(exc), "code": _NOT_CONFIGURED_CODE},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=_NOT_CONFIGURED_CODE,
                error_detail=str(exc),
            )
            return
        except LLMError as exc:
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": str(exc), "code": type(exc).__name__},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=type(exc).__name__,
                error_detail=str(exc),
            )
            return

        # 4. Emit ``llm.started`` deltas + the BEFORE_LLM_CALL hook.
        from magi.bus.stream import get_stream_hub as _get_hub
        hub = _get_hub()
        hub.publish(StreamEvent(run_id, attempt_id, 1, "llm.started", {}))
        self._emit_hook(
            "before",
            provider=provider,
            result=None,
            extra={"run_id": run_id, "attempt_id": attempt_id},
        )

        # 5. Call the provider. ``chat()`` for non-streaming,
        # ``stream()`` for incremental deltas. The hook observer
        # never sees mid-flight deltas (deltas flow via StreamHub
        # keyed by run_id, same as today).
        chat_messages = [
            ChatMessage(role=m["role"], content=m.get("content") or "")
            for m in request.get("messages") or []
        ]
        tools = list(request.get("tools") or []) or None
        streaming = bool(request.get("streaming"))
        max_tokens = int(request.get("max_tokens") or 1024)
        system = request.get("system")

        async def _forward(event) -> None:
            # ``event`` is a provider-emitted ``LLMStreamEvent``;
            # we re-emit on the StreamHub so WebSocket / SSE keeps
            # working unchanged. The sequence number here is opaque
            # to the worker — the hub consumer orders it.
            hub.publish(
                StreamEvent(
                    run_id,
                    attempt_id,
                    0,  # sequence is overwritten by hub consumers
                    f"llm.{event.kind}",
                    dict(event.payload),
                ),
            )

        try:
            if streaming:
                result = await provider.stream(
                    system=system,
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    tools=tools,
                    on_event=_forward,
                )
            else:
                result = await provider.chat(
                    system=system,
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    tools=tools,
                )
        except LLMNotConfiguredError as exc:
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": str(exc), "code": _NOT_CONFIGURED_CODE},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=_NOT_CONFIGURED_CODE,
                error_detail=str(exc),
            )
            return
        except LLMError as exc:
            self._emit_hook("after", provider=provider, result=None,
                            error_code=type(exc).__name__, error_detail=str(exc))
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": str(exc), "code": type(exc).__name__},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=type(exc).__name__,
                error_detail=str(exc),
            )
            return

        # 6. Terminal write — success. Result JSON shape matches
        # the columns BusStore already reads for ``complete_agent_message``
        # / ``wait_for_tools`` backwards-compat (today those methods
        # read off the row); Phase D will move those reads onto
        # ``ProviderJobResult`` semantics.
        self.store.complete_llm_attempt(
            attempt_id,
            response=self._response_payload(result),
        )
        self._emit_hook("after", provider=provider, result=result)
        self._publish_completion(run_id, attempt_id, "completed")

    # ----- helpers ------------------------------------------------------

    @staticmethod
    def _response_payload(result: ChatResult) -> dict[str, Any]:
        return {
            "text": result.text,
            "thinking": result.thinking,
            "tool_uses": list(result.tool_uses),
            "raw_blocks": list(result.raw_blocks),
            "model": result.model,
            "usage": result.usage,
            "stop_reason": result.stop_reason,
        }

    @staticmethod
    def _emit_hook(
        stage: str,
        *,
        provider: LLMProvider,
        result: ChatResult | None,
        error_code: str | None = None,
        error_detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Fire the BEFORE / AFTER plugin hook for this call.

        The hook subsystem is currently being rewritten (the legacy
        ``Hook`` enum + ``PluginContext`` were redesignated as
        ``HookPoint`` + ``HookEnvelope`` upstream). Until the new
        emit channel is wired, this method is a **single best-effort
        fire site**: it tries the legacy ``emit(...)`` entry-point
        first, falls through to no-op if the symbols aren't present,
        and never lets a missing plug-in surface crash the provider
        worker. The contract is unchanged — the worker remains the
        *only* place every LLM call passes through, so plugins that
        observe the request/response emit from here.
        """
        try:
            from magi.plugins import emit as _emit_hook_legacy
            from magi.plugins import PluginContext, Hook
        except Exception:
            return  # legacy hook surface not installed; nothing to do.
        try:
            if stage == "before":
                ctx = PluginContext(
                    hook=Hook.BEFORE_LLM_CALL,
                    llm_provider=provider.name or provider.__class__.__name__,
                    llm_model=provider.model,
                )
            else:
                usage = (result.usage if result is not None else None) or {}
                ctx = PluginContext(
                    hook=Hook.AFTER_LLM_CALL,
                    llm_provider=provider.name or provider.__class__.__name__,
                    llm_model=result.model if result is not None else provider.model,
                    llm_input_tokens=_safe_int(usage.get("input_tokens")),
                    llm_output_tokens=_safe_int(usage.get("output_tokens")),
                    llm_error=error_detail if error_code else None,
                )
            _emit_hook_legacy(ctx.hook, ctx)
        except Exception:
            # Defensive — never let a plugin surface crash an LLM call.
            logger.exception(
                "providers worker: hook emit failed (stage=%s, attempt-id=%s); ignoring",
                stage, getattr(provider, "model", "?"),
            )

    def _publish_completion(
        self,
        run_id: str,
        attempt_id: str,
        status: str,  # "completed" | "failed"
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Publish a ``provider.completed`` AgentInbox event to wake the agent."""
        msg = AgentMessage(
            event_id=f"provider-result:{attempt_id}",
            text="",
            channel="agent.internal",
            session_id=None,
            uid=None,
            kind=_provider_completed_kind(),
            target_run_id=run_id,
            metadata={
                "attempt_id": attempt_id,
                "status": status,
                "error_code": error_code,
                "error_detail": error_detail,
            },
        )
        try:
            self.store.publish_agent_message(msg)
        except Exception:
            logger.exception(
                "providers worker: failed to publish %s for attempt %s",
                status, attempt_id,
            )


def _provider_completed_kind() -> InboxKind:
    """Cast the string literal — keeps the type-checker honest."""
    return "provider.completed"  # type: ignore[return-value]


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lazy_get_bus_store() -> BusStoreProtocol:
    """Resolve the process-global ``BusStore`` lazily.

    Importing ``magi.bus.bootstrap`` at module load would trigger a
    SQLAlchemy model double-registration against
    ``Base.metadata`` (see top-of-file note). Defer the import to
    first use; by then the runtime's own ``bootstrap()`` has run
    and the model is registered exactly once.
    """
    from magi.bus.bootstrap import get_bus_store as _get
    return _get()


# ----- module-level singletons ---------------------------------------


_worker: ProvidersWorker | None = None


async def start_provider_worker(
    *, store: BusStoreProtocol | None = None,
) -> ProvidersWorker:
    """Start the process-local provider worker after SQLite is ready."""
    global _worker
    if _worker is None:
        _worker = ProvidersWorker(store=store)
        await _worker.start()
    return _worker


async def stop_provider_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


async def enqueue_provider_job(job: ProviderJob) -> str:
    """Publish-side helper used by Phase D callers.

    Inserts the queued row and writes the serialized request JSON so
    the worker can read it back via
    :meth:`magi.bus.store.BusStore.load_provider_job_request`.
    Wakes the local worker (no-op across processes; that's fine —
    the poller will pick the row up on its next tick).
    """
    store = _lazy_get_bus_store()
    attempt_id = store.enqueue_provider_job(
        run_id=job.run_id,
        inbox_event_id=job.inbox_event_id,
        kind=job.kind,
    )
    request = {
        "system": job.system,
        "messages": list(job.messages),
        "max_tokens": job.max_tokens,
        "tools": list(job.tools) if job.tools else None,
        "streaming": job.streaming,
        # Carry ``extra`` through so callers (auto_title in Phase D)
        # can recover context when reading the result back without
        # an extra DB lookup.
        "extra": dict(job.extra),
    }
    store.persist_provider_job_request(attempt_id, request=request)
    if _worker is not None:
        _worker.notify()
    return attempt_id


__all__ = [
    "ProvidersWorker",
    "start_provider_worker",
    "stop_provider_worker",
    "enqueue_provider_job",
]
