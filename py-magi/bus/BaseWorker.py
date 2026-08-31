"""BaseWorker: a listen loop on the shared BUS event loop."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from concurrent.futures import Future
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar

from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.go import go

if TYPE_CHECKING:
    from .bus import Bus

logger = logging.getLogger("bus.worker")


class BaseWorker:
    """A BUS-facing listen loop. Attach starts it; detach stops it.

    The loop itself is scheduled with :func:`~bus.base.go.go`. Override
    :meth:`_poll` to claim work: return True to skip the idle sleep.
    ``go()`` anything that should not block the next claim.

    Declare :attr:`default_settings` on the subclass. ``__init__`` boosts
    those defaults onto the BUS. Startup parameters are boosted by
    :meth:`Bus.attach` before this worker starts.
    """

    worker_name: ClassVar[str | None] = None
    default_settings: ClassVar[Mapping[str, str]] = {}

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        self.poll_seconds = poll_seconds
        self.worker_id: str | None = None
        self._bus = bus
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._attached_ok = False
        self._running: Future[Any] | None = None
        self._boost_defaults()

    def attach(self) -> bool:
        """Start this worker's listen loop on its BUS."""
        if self._running is not None and not self._running.done():
            return True
        self.worker_id = type(self).worker_name
        self._stop.clear()
        self._ready.clear()
        self._attached_ok = False
        self._running = go(self._serve_loop())
        if self._ready.wait(timeout=5.0) and self._attached_ok:
            return True
        self.detach()
        return False

    def detach(self) -> None:
        """Stop the listen loop. Work already passed to ``go()`` keeps running."""
        self._stop.set()
        future = self._running
        self._running = None
        if future is not None and not future.done():
            future.cancel()
            with suppress(Exception):
                future.result(timeout=self.poll_seconds + 1.0)
        self.worker_id = None

    def is_alive(self) -> bool:
        future = self._running
        return future is not None and not future.done()

    @property
    def bus(self) -> Bus:
        """The BUS this worker was constructed with."""
        return self._bus

    def _boost_defaults(self) -> None:
        defaults = dict(type(self).default_settings)
        worker_name = type(self).worker_name
        if not defaults or not worker_name:
            return
        if not self.bus.boost_default_settings(worker_name=worker_name, settings=defaults):
            raise RuntimeError(f"{worker_name} worker: settings boost failed")

    async def on_attached(self) -> None:
        """Optional async initialization after the BUS is attached."""

    async def on_detached(self) -> None:
        """Optional async cleanup after the listen loop has stopped."""

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        """Return the mounted JobBoard for *job_type*."""
        return self.bus.board(job_type)

    async def claim[JobT: BaseJob](self, job_type: type[JobT]) -> JobT | None:
        """Claim one pending Job of *job_type*, off the listen loop."""
        return await self.call(self.board(job_type).claim)

    def publish_notify(self, job: BaseJob) -> None:
        """Enqueue a Notify without waiting for its id."""
        go(asyncio.to_thread(self.board(type(job)).publish, job))

    def publish(self, job: BaseJob) -> int:
        """Enqueue *job* and return its id."""
        return self.board(type(job)).publish(job)

    async def ask(self, job: BaseJob) -> BaseJobResult:
        """Publish *job* and return its completed result.

        Times out or a ``FAILED`` status raise ``RuntimeError``.
        """
        board = self.board(type(job))
        result = await self.call(board.get_result, await self.call(self.publish, job))
        if result is not None and result.status is JobStatus.COMPLETED:
            return result
        raise RuntimeError(
            (result.error if result is not None else None)
            or f"{type(job).__name__} result is unavailable"
        )

    def submit(self, job_type: type[BaseJob], result: BaseJobResult) -> None:
        """Accept a Job result without waiting."""
        go(self.board(job_type).submit_result(result))

    async def call(self, fn, /, *args, **kwargs):
        """Run a synchronous BUS operation away from the listen loop."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _poll(self) -> bool:
        """Claim and dispatch one unit of work. True skips the idle sleep."""
        return False

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if await self._poll():
                    continue
            except Exception:  # noqa: BLE001 -- a BUS blip must not kill the loop
                logger.exception("%s worker: BUS operation failed", self.worker_name or "worker")
            await asyncio.sleep(self.poll_seconds)

    async def _serve_loop(self) -> None:
        try:
            try:
                await self.on_attached()
            except Exception:  # noqa: BLE001 -- attach must report failure to the runtime
                self._attached_ok = False
                return
            self._attached_ok = True
            self._ready.set()
            await self._run()
        finally:
            self._ready.set()
            with suppress(Exception):
                await self.on_detached()
