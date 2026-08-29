"""Shared runtime-worker lifecycle and observability primitives.

Workers own domain behaviour; this module owns the mechanics common to every
long-lived MAGI worker: lifecycle, tracked child tasks, blocking BUS calls,
and health reporting.  It deliberately does not impose a job-board protocol.

Lives at the package root (not under :mod:`magi.startup`) so that
domain workers can depend on a neutral foundation primitive without
pulling the composition root into their import graph.  The previous
location (:mod:`magi.startup.worker`) created a runtime dependency
cycle::

    bus.library.file.skillsBook ──▶ startup.paths
    startup                      ──▶ domain workers
    domain workers               ──▶ startup.worker.RuntimeWorker

Putting :class:`RuntimeWorker` here breaks the
``startup -> ... -> startup`` leg while leaving :mod:`magi.startup`
free to do nothing but compose the workers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.runtime_worker")
T = TypeVar("T")


class RuntimeWorker(ABC):
    """Common lifecycle and bounded in-process concurrency for one worker.

    A runtime owns exactly one instance of each worker kind.  ``concurrency``
    limits work *inside* that instance; it does not create a second consumer.
    Domain workers reserve a slot before claiming durable work, then hand the
    reserved slot to :meth:`spawn_reserved`.  This keeps a job from sitting in
    ``PROCESSING`` merely because all local execution slots are busy.
    """

    worker_name = "worker"
    worker_kind = "runtime"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        self.bus = bus
        self.poll_seconds = poll_seconds
        if concurrency is not None and concurrency < 1:
            raise ValueError("concurrency must be positive")
        self.concurrency = concurrency or 2
        self._slots = asyncio.Semaphore(self.concurrency)
        # The durable lease owner must identify this runtime instance, not a
        # Board object or a worker class.  A restarted worker therefore cannot
        # submit a stale result for a lease that a successor reclaimed.
        self.worker_id = f"{self.worker_name}-{uuid.uuid4().hex}"
        self._task: asyncio.Task[None] | None = None
        self._children: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._last_poll_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    async def start(self) -> bool:
        """Start the loop and return whether this worker actually started.

        ``on_start`` may return ``False`` for an intentionally unavailable
        worker (for example Telegram before a token is configured).  That is
        not a failed startup and must not reserve a registry slot.
        """
        if self._task is not None:
            return True
        self._stopping = False
        if await self.on_start() is False:
            return False
        self._task = asyncio.create_task(
            self._run_guarded(), name=f"magi-{self.worker_name}-worker"
        )
        return True

    async def stop(self) -> None:
        self._stopping = True
        await self.on_stop_requested()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._children:
            children = tuple(self._children)
            for task in children:
                task.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            self._children.clear()
        await self.on_stopped()

    async def on_start(self) -> bool | None:
        """Optional startup hook; return ``False`` to intentionally skip."""
        return None

    async def on_stop_requested(self) -> None:
        """Optional signal step before the main loop is cancelled."""
        return None

    async def on_stopped(self) -> None:
        """Optional cleanup after all owned tasks have stopped."""
        return None

    @abstractmethod
    async def _run(self) -> None:
        """Run until ``_stopping`` is true or cancellation is requested."""

    async def _run_guarded(self) -> None:
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive lifecycle guard
            self._last_error = str(exc)
            logger.exception("worker %s stopped unexpectedly", self.worker_name)

    async def call(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run a synchronous BUS/Book operation away from the event loop."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    def spawn(self, coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        """Create and retain an owned child task for deterministic shutdown."""
        task = asyncio.create_task(coro, name=name)
        self._children.add(task)
        task.add_done_callback(self._children.discard)
        return task

    async def reserve_capacity(self) -> None:
        """Reserve one bounded execution slot before claiming a job.

        The caller must either pass the reservation to
        :meth:`spawn_reserved` or call :meth:`release_capacity` if it did not
        obtain work.  Keeping the two operations explicit makes queue claim
        loops auditable: an unstarted task must never hold a durable lease.
        """
        await self._slots.acquire()

    def release_capacity(self) -> None:
        """Release an unused reservation made by :meth:`reserve_capacity`."""
        self._slots.release()

    def spawn_reserved(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        """Run *coro* under an already-reserved concurrency slot."""

        async def _run_and_release() -> T:
            try:
                return await coro
            finally:
                self.release_capacity()

        return self.spawn(_run_and_release(), name=name)

    def polled(self) -> None:
        self._last_poll_at = datetime.now(UTC)

    def succeeded(self) -> None:
        self._last_success_at = datetime.now(UTC)
        self._last_error = None

    def failed(self, exc: BaseException | str) -> None:
        self._last_error = str(exc)

    def queue_depth(self) -> int | None:
        return None

    def health(self) -> dict[str, object]:
        return {
            "name": self.worker_name,
            "kind": self.worker_kind,
            "running": self._task is not None and not self._task.done(),
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error": self._last_error,
            "inflight": len(self._children),
            "concurrency": self.concurrency,
            "queue_depth": self.queue_depth(),
        }


__all__ = ["RuntimeWorker"]
