"""Durable provider consumer owned by :mod:`magi.providers`.

The :class:`ProvidersWorker` is the **single** place every LLM
API call in MAGI goes through. It owns:

- the durable queue (``llm_attempts`` rows with ``status="queued"``
  the agent loop writes via :func:`enqueue_llm_job`);
- the streaming deltas (``StreamHub`` publishes keyed by ``run_id``
  so existing WebSocket / SSE consumers don't need to know about
  the worker).

Design principle
================

The worker is **a dumb LLM invoker**. It does not know whether the
queued job came from an agent turn, chat-history compaction, or a
session-title job. The origin caller reads the result off the
``LLMAttempt.response`` row (or via the ``provider.completed`` inbox
event) and does its own post-processing in its own layer.

Hook firing
===========

The worker does NOT fire hooks itself.  All hook dispatching
happens inside the bus.store boundary methods:
``enqueue_llm_job`` stamps a ``hook_signoffs`` row per enabled
plugin subscribed to ``LLM_REQUEST_PREPARED``; ``complete_llm_attempt``
stamps one for ``LLM_RESPONSE_RECEIVED``.  The provider worker
is the *last* thing that touches the LLM row in the lifecycle
because it is filtered by ``claim_next_llm_job`` until every
plugin has acked its signoff.

The provider worker is hook-agnostic: it never imports
``magi.plugins`` or ``magi.bus.hooks``.  Plugins that want to
observe LLM I/O consume the ``hook_signoffs`` queue directly via
``bus.store.claim_pending_signoffs(plugin_id)`` --
they see every request and response through the BUS, not the
worker.
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
# would transitively register ``magi.bus.db.models.local.hook_evaluation``
# which carries a SQLAlchemy ``Mapped`` named ``metadata`` -- that
# collides with the reserved ``Base.metadata`` on SQLAlchemy's
# declarative API. The double-registration trips the error any
# time ``magi.bus.bootstrap`` is statically imported after the
# app's own bootstrap has already registered the model. Touching
# only the leaf modules here keeps the worker module load cheap
# and lets the runtime's first ``bootstrap()`` call register each
# model exactly once.
from magi.bus.protocols.agent import AgentMessage, BusStoreProtocol, InboxKind
from magi.bus.protocols.control_jobs import PROVIDER_CONFIG_CHANGED
from magi.bus.protocols.llm_jobs import LLMJob
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
        # Cached LLM client + the typed error that prevented us from
        # building one (so a job claimed with no config can settle as
        # ``failed`` with the operator-facing envelope). Populated in
        # ``_rebuild_provider``; rebuilt when ``control_jobs`` carries a
        # ``provider.config_changed`` row. ``self._provider`` is read by
        # every parallel job slot and only written from ``_run``, so no
        # lock is needed.
        self._provider: LLMProvider | None = None
        self._provider_error: LLMError | None = None

    # ----- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        recovered = self.store.recover_expired_llm_job_leases()
        if recovered:
            logger.warning(
                "providers worker: recovered %d expired leases at boot",
                recovered,
            )
        # Resolve the cached provider *before* the run loop starts so a
        # missing config cannot block boot; the failure path lives in
        # ``_rebuild_provider`` (logs + records ``_provider_error``).
        self._rebuild_provider()
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
        self._provider = None
        self._provider_error = None

    def notify(self) -> None:
        """Wake the poller after an in-process publish."""
        self._wake.set()

    # ----- main loop ----------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            # Drain transient control signals before polling LLM
            # jobs. ``provider.config_changed`` is the only kind today;
            # ``drain_control_jobs`` returns 0 cheaply when the queue
            # is empty. Multiple queued rows coalesce into one rebuild
            # (the count is what matters, not the rows themselves).
            if self.store.drain_control_jobs(
                worker_id=self.worker_id,
                kind=PROVIDER_CONFIG_CHANGED,
            ):
                self._rebuild_provider()
            claim = self.store.claim_next_llm_job(self.worker_id)
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
        except Exception as exc:  # noqa: BLE001 -- worker MUST NOT crash the bus
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
        # 1. Re-check deadline -- fail-fast if the run already expired
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
        request = self.store.load_llm_job_request(attempt_id)
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

        # 3. Resolve provider from the cache populated by
        # ``_rebuild_provider`` (called in ``start()`` and after every
        # drained ``provider.config_changed`` row). ``self._provider``
        # is the immutable SDK client; ``self._provider_error`` carries
        # the typed error from the last build attempt so a job claimed
        # while config is missing settles with the right envelope.
        provider = self._provider
        if provider is None:
            exc = self._provider_error or LLMNotConfiguredError(
                "MAGI runtime has no LLM provider / API key configured; "
                "set it in MAGI management",
            )
            error_code = (
                _NOT_CONFIGURED_CODE
                if isinstance(exc, LLMNotConfiguredError)
                else type(exc).__name__
            )
            self.store.complete_llm_attempt(
                attempt_id,
                error={"detail": str(exc), "code": error_code},
            )
            self._publish_completion(
                run_id, attempt_id, "failed",
                error_code=error_code,
                error_detail=str(exc),
            )
            return

        # 4. Publish ``llm.started`` deltas.  The LLM_REQUEST_PREPARED
        # hook already fired at enqueue time (before the worker was
        # bound); the worker doesn't re-fire it.
        from magi.bus.stream import get_stream_hub as _get_hub
        hub = _get_hub()
        hub.publish(StreamEvent(run_id, attempt_id, 1, "llm.started", {}))

        # 5. Call the provider. ``chat()`` for non-streaming,
        # ``stream()`` for incremental deltas.  The LLM_RESPONSE_RECEIVED
        # hook fires automatically when ``complete_llm_attempt`` is
        # called below (see bus.store.bus_store.py).
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
            # to the worker -- the hub consumer orders it.
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

        # 6. Terminal write -- success. ``complete_llm_attempt`` fires
        # the LLM_RESPONSE_RECEIVED OBSERVE hook (when the caller
        # attached a hook_context to the LLMJob).
        self.store.complete_llm_attempt(
            attempt_id,
            response=self._response_payload(result),
        )
        self._publish_completion(run_id, attempt_id, "completed")

    # ----- helpers ------------------------------------------------------

    def _rebuild_provider(self) -> None:
        """(Re)build the cached ``LLMProvider`` from the current config.

        Synchronous: ``get_provider`` opens a SQLAlchemy session and
        reads ``runtime_settings.toml``, then constructs the SDK
        client. None of those steps do network I/O, so there's no
        reason to defer to a thread. Tests inject a fake via the
        module-level ``magi.providers.worker.get_provider`` binding,
        so this method reads through that name (re-bound through the
        package's ``__init__``) to keep the patch seam working.

        Never raises: a missing / invalid config logs once and leaves
        ``self._provider = None`` so the next claimed job settles
        with the operator-facing envelope instead of crashing the
        worker.
        """
        try:
            provider = get_provider()
        except LLMNotConfiguredError as exc:
            self._provider = None
            self._provider_error = exc
            logger.warning(
                "providers worker: no LLM configured (%s); jobs will fail-fast",
                exc,
            )
            return
        except LLMError as exc:
            self._provider = None
            self._provider_error = exc
            logger.warning(
                "providers worker: cannot build LLM (%s); jobs will fail-fast",
                exc,
            )
            return
        self._provider = provider
        self._provider_error = None
        logger.info(
            "providers worker: cached LLM client (%s)",
            type(provider).__name__,
        )

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
    """Cast the string literal -- keeps the type-checker honest."""
    return "provider.completed"  # type: ignore[return-value]


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


async def enqueue_llm_job(job: LLMJob) -> str:
    """Publish-side helper used by Phase D callers.

    Inserts the queued row and writes the serialized request JSON so
    the worker can read it back via
    :meth:`magi.bus.store.BusStore.load_llm_job_request`.
    Wakes the local worker (no-op across processes; that's fine --
    the poller will pick the row up on its next tick).

    Hook dispatching is handled inside
    :meth:`magi.bus.store.BusStore.enqueue_llm_job`: it stamps a
    ``hook_signoffs`` row per enabled plugin subscribed to
    ``LLM_REQUEST_PREPARED`` so plugin workers observe the row
    before the provider worker claims it.  The worker is filtered
    by ``claim_next_llm_job`` until every plugin has acked its
    signoff, so the LLM call cannot run until plugin observers have
    had their chance.
    the row up -- the caller (agent turn) observes the DENY and
    falls back to a synthetic response.
    """
    store = _lazy_get_bus_store()
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
    # The new bus.store.enqueue_llm_job signature takes ``request``
    # in the same call so the GATE materializer can read the LLM
    # request payload.  When the bus.store retro-fit isn't in place
    # yet, fall back to the old two-step enqueue + persist.
    try:
        result = store.enqueue_llm_job(
            run_id=job.run_id,
            request=request,
            inbox_event_id=job.inbox_event_id,
            kind=job.kind,
            hook_context=job.hook_context,
        )
        attempt_id = result.row_id
    except TypeError:
        # Legacy bus.store (pre-hook-firing): no ``request`` / no
        # ``hook_context`` kwargs.  Fall back to the two-step path.
        attempt_id = store.enqueue_llm_job(
            run_id=job.run_id,
            inbox_event_id=job.inbox_event_id,
            kind=job.kind,
        )
        store.persist_llm_job_request(attempt_id, request=request)
    if _worker is not None:
        _worker.notify()
    return attempt_id


__all__ = [
    "ProvidersWorker",
    "start_provider_worker",
    "stop_provider_worker",
    "enqueue_llm_job",
]
