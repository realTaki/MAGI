"""BaseWorker: a listen loop on the shared BUS event loop."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import Future
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar

from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.go import go

if TYPE_CHECKING:
    from .bus import Bus

_JOIN_TIMEOUT = 2.0


class BaseWorker:
    """A BUS-facing listen loop. Attach starts it; detach stops it.

    The loop itself is scheduled with :func:`~bus.base.go.go`. Override
    :meth:`_poll` to claim work: return True to skip the idle sleep.
    ``go()`` anything that should not block the next claim.

    Declare :attr:`default_settings` on the subclass. :meth:`Bus.attach`
    writes startup parameters first; :meth:`attach` then fills any
    missing defaults.
    """

    worker_name: ClassVar[str]
    default_settings: ClassVar[Mapping[str, str]] = {}

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        self.poll_seconds = poll_seconds
        self.bus = bus
        self._running: Future[Any] | None = None

    def attach(self) -> bool:
        """Fill missing defaults, run ``on_attached``, then start the listen loop."""
        if self.is_alive():
            return True
        defaults = dict(type(self).default_settings)
        if defaults:
            self.bus.boost_default_settings(worker_name=type(self).worker_name, settings=defaults)
        starting = go(self.on_attached())
        try:
            starting.result(timeout=5.0)
        except Exception:
            self._cancel(starting)
            with suppress(Exception):
                go(self.on_detached()).result(timeout=_JOIN_TIMEOUT)
            return False
        self._running = go(self._run())
        return True

    def detach(self) -> None:
        """Stop the listen loop. Work already passed to ``go()`` keeps running."""
        future = self._running
        self._running = None
        self._cancel(future)

    def _cancel(self, future: Future[Any] | None) -> None:
        if future is None or future.done():
            return
        future.cancel()
        with suppress(Exception):
            future.result(timeout=_JOIN_TIMEOUT)

    def is_alive(self) -> bool:
        future = self._running
        return future is not None and not future.done()

    async def on_attached(self) -> None:
        """Optional async initialization after the BUS is attached."""

    async def on_detached(self) -> None:
        """Optional async cleanup after the listen loop has stopped."""

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any] | None:
        """Return the mounted JobBoard for *job_type*, or None if it is not mounted."""
        return self.bus.board(job_type)

    async def claim[JobT: BaseJob](self, job_type: type[JobT]) -> JobT | None:
        """Claim one pending Job of *job_type*, off the listen loop."""
        board = self.board(job_type)
        return None if board is None else await self.call(board.claim)

    def publish_notify(self, job: BaseJob) -> None:
        """Enqueue a Notify without waiting for its id."""
        board = self.board(type(job))
        if board is not None:
            go(asyncio.to_thread(board.publish, job))

    def publish(self, job: BaseJob) -> int | BaseJobResult | None:
        """Publish a Job, returning its id or a direct Book-operation result."""
        board = self.board(type(job))
        return None if board is None else board.publish(job)

    async def ask[ResultT: BaseJobResult](self, job: BaseJob[ResultT]) -> ResultT | None:
        """Publish *job* and return its completed result, or None if it did not complete."""
        board = self.board(type(job))
        if board is None:
            return None
        published = await self.call(board.publish, job)
        if isinstance(published, BaseJobResult):
            return published if published.status is JobStatus.COMPLETED else None
        result = await self.call(board.get_result, published)
        if result is not None and result.status is JobStatus.COMPLETED:
            return result
        return None

    def submit(self, job_type: type[BaseJob], result: BaseJobResult) -> None:
        """Accept a Job result without waiting."""
        board = self.board(job_type)
        if board is not None:
            board.submit_result(result)

    async def call(self, fn, /, *args, **kwargs):
        """Run a synchronous BUS operation away from the listen loop."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _poll(self) -> bool:
        """Claim and dispatch one unit of work. True skips the idle sleep."""
        return False

    async def _run(self) -> None:
        try:
            while True:
                if await self._poll():
                    continue
                await asyncio.sleep(self.poll_seconds)
        finally:
            with suppress(Exception):
                await self.on_detached()
