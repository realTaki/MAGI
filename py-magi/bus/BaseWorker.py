"""BaseWorker: a listen loop on the shared BUS event loop."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar

from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult
from .base.go import go

if TYPE_CHECKING:
    from .bus import Bus

logger = logging.getLogger("bus.worker")


class BaseWorker:
    """A BUS-facing listen loop. Attach starts it; detach stops it.

    The loop itself is scheduled with :func:`~bus.base.go.go`. Override
    :meth:`_poll` to claim work: return True to skip the idle sleep.
    ``go()`` anything that should not block the next claim.
    """

    worker_name: ClassVar[str | None] = None

    def __init__(self, *, poll_seconds: float = 0.25) -> None:
        self.poll_seconds = poll_seconds
        self.worker_id: str | None = None
        self.bus: Bus | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._attached_ok = False
        self._running: Future[Any] | None = None

    def attach(self, bus: Bus) -> bool:
        """Bind this worker to the runtime BUS and start its loop."""
        if self.bus is not None:
            return self.bus is bus
        self.bus = bus
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
        if self.bus is None:
            return
        self._stop.set()
        future = self._running
        self._running = None
        if future is not None and not future.done():
            future.cancel()
            with suppress(Exception):
                future.result(timeout=self.poll_seconds + 1.0)
        self._clear_attachment()

    def is_alive(self) -> bool:
        future = self._running
        return self.bus is not None and future is not None and not future.done()

    async def on_attached(self) -> None:
        """Optional async initialization after the BUS is attached."""

    async def on_detached(self) -> None:
        """Optional async cleanup after the listen loop has stopped."""

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        """Return the mounted JobBoard for *job_type*."""
        assert self.bus is not None
        return self.bus.board(job_type)

    async def claim[JobT: BaseJob](self, job_type: type[JobT]) -> JobT | None:
        """Claim one pending Job of *job_type*, off the listen loop."""
        return await self.call(self.board(job_type).claim)

    async def ask(self, job: BaseJob) -> BaseJobResult | None:
        """Publish *job* and wait for its written result."""
        board = self.board(type(job))
        job_id = await self.call(board.publish, job)
        return await board.get_result(job_id)

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
            except Exception:  # noqa: BLE001 -- attach must report failure to Magi
                self._attached_ok = False
                return
            self._attached_ok = True
            self._ready.set()
            await self._run()
        finally:
            self._ready.set()
            with suppress(Exception):
                await self.on_detached()

    def _clear_attachment(self) -> None:
        self.bus = None
        self.worker_id = None
        self._running = None
