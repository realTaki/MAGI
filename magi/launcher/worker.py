"""BaseWorker: lifecycle and Slot declaration for one runtime component."""

from __future__ import annotations

import importlib
import threading
from typing import ClassVar

from magi.new_bus import BusForWorker, Slot

_HEARTBEAT_INTERVAL = 0.25


def load_required_slots(worker_type: type[BaseWorker]) -> tuple[Slot, ...]:
    """Load ``REQUIRED_SLOTS`` from the worker package's ``requiredSlots.py``.

    Search order is the worker's own module, then its parent package, so both
    ``magi.agent.requiredSlots`` (class lives in ``magi.agent``) and
    ``magi.agent.worker`` + sibling ``requiredSlots.py`` resolve.
    """
    module_name = worker_type.__module__
    packages = (module_name, module_name.rpartition(".")[0])
    for package in packages:
        if not package:
            continue
        name = f"{package}.requiredSlots"
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name and name.endswith(error.name):
                continue
            raise
        slots = getattr(module, "REQUIRED_SLOTS", None)
        if slots is None:
            raise LookupError(f"{name} must export REQUIRED_SLOTS")
        return tuple(slots)
    raise LookupError(
        f"{worker_type.__qualname__} needs a requiredSlots.py next to the worker package"
    )


class BaseWorker:
    """A BUS-facing runtime component with attach + start/stop lifecycle.

    Slot requirements live in the worker package's ``requiredSlots.py``. A
    subclass may set ``required_slots`` to override that file (tests only).
    """

    required_slots: ClassVar[tuple[Slot, ...] | None] = None
    heartbeat_interval: float = _HEARTBEAT_INTERVAL

    def __init__(self) -> None:
        self.worker_id: str | None = None
        self.bus: BusForWorker | None = None
        self._running = False
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @classmethod
    def declared_slots(cls) -> tuple[Slot, ...]:
        for ancestor in cls.__mro__:
            if ancestor is BaseWorker or ancestor is object:
                continue
            override = ancestor.__dict__.get("required_slots")
            if override is not None:
                return tuple(override)
            try:
                return load_required_slots(ancestor)
            except LookupError:
                continue
        return load_required_slots(cls)

    def attach(self, bus_for_worker: BusForWorker) -> None:
        """Receive the BUS slice already allocated for this worker identity."""
        self.bus = bus_for_worker
        self.worker_id = bus_for_worker.worker_id

    def start(self) -> bool:
        """Start heartbeat and ``on_start``. Return False to abort launch."""
        if self._running:
            return True
        if self.bus is None or not self.bus.heartbeat():
            return False
        if self.on_start() is False:
            return False
        if not self.bus.heartbeat():
            return False
        self._stop.clear()
        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"magi-{self.worker_id}-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._running = False
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.heartbeat_interval + 1.0)
        self.on_stop()

    def on_start(self) -> bool | None:
        """Optional startup hook; return ``False`` to refuse to run."""
        return None

    def on_stop(self) -> None:
        """Optional cleanup after the heartbeat thread has stopped."""
        return None

    @property
    def is_running(self) -> bool:
        return self._running

    def is_alive(self) -> bool:
        return self.bus is not None and self.bus.is_alive()

    def health(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "running": self.is_running,
            "alive": self.is_alive(),
        }

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            bus = self.bus
            if bus is None or not bus.heartbeat():
                self._running = False
                return
