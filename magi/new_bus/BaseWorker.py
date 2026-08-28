"""BaseWorker: attach a BUS slice, detach to stop."""

from __future__ import annotations

import importlib
import threading
from typing import ClassVar

from .base.heartbeat import Slot
from .bus_for_worker import BusForWorker

_HEARTBEAT_INTERVAL = 0.25


class BaseWorker:
    """A BUS-facing component. Attach starts it; detach stops it.

    Slot requirements live in the worker package's ``requiredSlots.py``. A
    subclass may set ``required_slots`` to override that file in tests.
    """

    required_slots: ClassVar[tuple[Slot, ...] | None] = None
    heartbeat_interval: float = _HEARTBEAT_INTERVAL

    def __init__(self) -> None:
        self.worker_id: str | None = None
        self.bus: BusForWorker | None = None
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @classmethod
    def load_required_slots(cls) -> tuple[Slot, ...]:
        """Load this class's ``REQUIRED_SLOTS`` from ``requiredSlots.py``.

        Search the worker's own module first, then its parent package, so both
        ``magi.agent.requiredSlots`` and ``magi.agent.worker`` plus a sibling
        file work without Launcher knowing the worker's package layout.
        """
        module_name = cls.__module__
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
            f"{cls.__qualname__} needs a requiredSlots.py next to the worker package"
        )

    @classmethod
    def declared_slots(cls) -> tuple[Slot, ...]:
        for ancestor in cls.__mro__:
            if ancestor is BaseWorker or ancestor is object:
                continue
            override = ancestor.__dict__.get("required_slots")
            if override is not None:
                return tuple(override)
            try:
                return ancestor.load_required_slots()
            except LookupError:
                continue
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

    def is_attached(self) -> bool:
        return self.bus is not None

    def is_alive(self) -> bool:
        return self.bus is not None and self.bus.is_alive()

    def health(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "attached": self.is_attached(),
            "alive": self.is_alive(),
        }

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            bus = self.bus
            if bus is None or not bus.heartbeat():
                return

    def _clear_attachment(self) -> None:
        self.bus = None
        self.worker_id = None
