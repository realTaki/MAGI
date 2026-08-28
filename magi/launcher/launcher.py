"""Start one MAGI-BUS runtime and attach its workers."""

from __future__ import annotations

from collections.abc import Iterable

from magi.launcher.constant import DATABASE_URL, WORKERS
from magi.new_bus import BaseWorker, Bus, EngineFactory, Slot

_AND_DOCK_SLOTS = frozenset({"submit_post_publish", "submit_post_result"})


class Launcher:
    """The runtime entry point.

    ``run()`` follows the runtime's one startup sequence: open BUS, read all
    worker Slots, arrange Docks, create each ``BusForWorker``, then attach the
    worker.  ``shutdown()`` performs the inverse attachment cleanup.
    """

    def __init__(self) -> None:
        """Open the Runtime BUS from ``constant.DATABASE_URL``."""
        self.bus = Bus(EngineFactory(DATABASE_URL))
        self._workers: dict[str, BaseWorker] = {}

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def run(self) -> bool:
        """Open the configured workers on this runtime's BUS."""
        if self._workers:
            raise ValueError("already running")

        prepared: list[tuple[str, BaseWorker, tuple[Slot, ...]]] = []
        for worker_type in WORKERS:
            worker_id = worker_type.worker_name
            if not worker_id:
                raise ValueError(f"{worker_type.__qualname__} needs worker_name")
            prepared.append((worker_id, worker_type(), worker_type.declared_slots()))
        if not prepared:
            raise ValueError("no workers")
        if len({worker_id for worker_id, _, _ in prepared}) != len(prepared):
            raise ValueError("duplicate worker_id")

        if not self._install_docks(slots for _, _, slots in prepared):
            return False

        attached: dict[str, BaseWorker] = {}
        for worker_id, worker, slots in prepared:
            bus_for_worker = self.bus.for_worker(worker_id, slots)
            if bus_for_worker is None or not worker.attach(bus_for_worker):
                worker.detach()
                self._detach_workers(attached)
                return False
            attached[worker_id] = worker
        self._workers = attached
        return True

    def shutdown(self) -> None:
        """Unplug every worker this panel is holding."""
        self._detach_workers(self._workers)
        self._workers = {}

    def __enter__(self) -> Launcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
        self.bus.close()

    def _install_docks(self, all_slots: Iterable[tuple[Slot, ...]]) -> bool:
        requested: dict[Slot, int] = {}
        for slots in all_slots:
            for slot in slots:
                requested[slot] = requested.get(slot, 0) + 1
        for slot, count in requested.items():
            if count <= 1:
                continue
            install = (
                self.bus.install_and_dock
                if slot.name in _AND_DOCK_SLOTS
                else self.bus.install_or_dock
            )
            if not install(slot):
                return False
        return True

    @staticmethod
    def _detach_workers(workers: dict[str, BaseWorker]) -> None:
        for worker in reversed(tuple(workers.values())):
            worker.detach()
