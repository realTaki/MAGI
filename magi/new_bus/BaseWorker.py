"""BaseWorker: attach a BUS slice, detach to stop."""

from __future__ import annotations

import threading
from typing import ClassVar

from .base.heartbeat import Slot
from .bus_for_worker import BusForWorker

_HEARTBEAT_INTERVAL = 0.25


class BaseWorker:
    """A BUS-facing component. Attach starts it; detach stops it.

    Subclasses import their own ``requiredSlots.REQUIRED_SLOTS`` onto
    ``required_slots``. Tests may set ``required_slots`` on a subclass to
    override that list.
    """

    worker_name: ClassVar[str | None] = None
    required_slots: ClassVar[tuple[Slot, ...] | None] = None
    heartbeat_interval: float = _HEARTBEAT_INTERVAL

    def __init__(self) -> None:
        self.worker_id: str | None = None
        self.bus: BusForWorker | None = None
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @classmethod
    def load_required_slots(cls) -> tuple[Slot, ...]:
        """Return this class's imported ``required_slots``."""
        if cls.required_slots is None:
            raise LookupError(f"{cls.__qualname__} must set required_slots")
        return tuple(cls.required_slots)

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
        return True

    def detach(self) -> None:
        """Drop the BUS slice and stop the Slot lease heartbeat.

        Subclasses that own extra threads should stop them first, then call
        ``super().detach()``.
        """
        if self.bus is None:
            return
        self._stop.set()
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.heartbeat_interval + 1.0)
        self._clear_attachment()

    def is_alive(self) -> bool:
        return self.bus is not None and self.bus.is_alive()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            bus = self.bus
            if bus is None or not bus.heartbeat():
                return

    def _clear_attachment(self) -> None:
        self.bus = None
        self.worker_id = None
