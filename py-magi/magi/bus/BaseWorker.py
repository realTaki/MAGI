"""BaseWorker: vNext worker lifecycle and bounded async execution."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any, ClassVar, TypeVar

from .base.heartbeat import Slot
from .bus_for_worker import BusForWorker

_HEARTBEAT_INTERVAL = 0.25

T = TypeVar("T")


class BaseWorker:
    """A BUS-facing component. Attach starts it; detach stops it.

    Subclasses import their own ``requiredSlots.REQUIRED_SLOTS`` onto
    ``required_slots``. Every worker gets the same event-loop, child-task, and
    bounded-concurrency mechanics; a worker that consumes Jobs overrides
    :meth:`_run` and uses ``reserve_capacity`` / ``spawn_reserved``.
    """

    worker_name: ClassVar[str | None] = None
    required_slots: ClassVar[tuple[Slot, ...] | None] = None
    heartbeat_interval: float = _HEARTBEAT_INTERVAL

    def __init__(
        self,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        if concurrency is not None and concurrency < 1:
            concurrency = 1
        self.poll_seconds = poll_seconds
        self.concurrency = concurrency or 2
        self.worker_id: str | None = None
        self.bus: BusForWorker | None = None
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._slots: asyncio.Semaphore | None = None
        self._children: set[asyncio.Task[Any]] = set()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._attached_ok = False

    @classmethod
    def load_required_slots(cls) -> tuple[Slot, ...]:
        """Return this class's imported ``required_slots``."""
        return tuple(cls.required_slots or ())

    @classmethod
    def declared_slots(cls) -> tuple[Slot, ...]:
        return cls.load_required_slots()

    def attach(self, bus_for_worker: BusForWorker) -> bool:
        """Bind this worker to a BUS slice and keep its Slot lease alive.

        Launcher has already allocated the declared Slots. Heartbeat is
        internal to the attachment; there is no separate start step.
        """
        if self.bus is not None:
            return self.bus is bus_for_worker
        self.bus = bus_for_worker
        self.worker_id = bus_for_worker.worker_id
        self._stop.clear()
        if not bus_for_worker.heartbeat():
            self._clear_attachment()
            return False
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"magi-{self.worker_id}-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._ready.clear()
        self._attached_ok = False
        self._loop_thread = threading.Thread(
            target=self._thread_main,
            name=f"magi-{self.worker_id}-{self.worker_name}",
            daemon=True,
        )
        self._loop_thread.start()
        if self._ready.wait(timeout=5.0) and self._attached_ok:
            return True
        self.detach()
        return False

    def detach(self) -> None:
        """Drop the BUS slice and stop the Slot lease heartbeat.

        All worker threads and child tasks belong to this base class.
        """
        if self.bus is None:
            return
        self._stop.set()
        loop = self._event_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_children)
        loop_thread = self._loop_thread
        self._loop_thread = None
        if loop_thread is not None and loop_thread is not threading.current_thread():
            loop_thread.join(timeout=self.poll_seconds + 1.0)
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.heartbeat_interval + 1.0)
        self._clear_attachment()

    def is_alive(self) -> bool:
        return self.bus is not None and self.bus.is_alive()

    async def on_attached(self) -> None:
        """Optional async initialization after the BUS slice is attached."""

    async def on_detached(self) -> None:
        """Optional async cleanup after all owned work has stopped."""

    async def call(self, fn, /, *args, **kwargs):
        """Run a synchronous BUS operation away from this worker's loop."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def reserve_capacity(self) -> None:
        assert self._slots is not None
        await self._slots.acquire()

    def release_capacity(self) -> None:
        assert self._slots is not None
        self._slots.release()

    def spawn_reserved(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        """Run a claimed job and release the reservation when it settles."""

        async def run_and_release() -> T:
            try:
                return await coro
            finally:
                self.release_capacity()

        task = asyncio.create_task(run_and_release(), name=name)
        self._children.add(task)
        task.add_done_callback(self._children.discard)
        return task

    async def _run(self) -> None:
        """Keep an attached worker alive when it has no Job loop of its own."""
        while not self._stop.is_set():
            await asyncio.sleep(self.poll_seconds)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            bus = self.bus
            if bus is None or not bus.heartbeat():
                return

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:  # noqa: BLE001 -- one worker must not kill Launcher
            self._attached_ok = False
            self._stop.set()
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        self._slots = asyncio.Semaphore(self.concurrency)
        try:
            try:
                await self.on_attached()
            except Exception:  # noqa: BLE001 -- attach must report failure to Launcher
                self._attached_ok = False
                return
            self._attached_ok = True
            self._ready.set()
            await self._run()
        finally:
            self._cancel_children()
            if self._children:
                await asyncio.gather(*tuple(self._children), return_exceptions=True)
            with suppress(Exception):
                await self.on_detached()
            self._event_loop = None

    def _cancel_children(self) -> None:
        for task in tuple(self._children):
            task.cancel()

    def _clear_attachment(self) -> None:
        self.bus = None
        self.worker_id = None
