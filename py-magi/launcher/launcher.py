"""Start one MAGI-BUS runtime and attach its workers."""

from __future__ import annotations

from bus import BaseWorker, Bus, SlotTag
from launcher.constant import WORKERS, WORKSPACE_PATH


class Launcher:
    """The runtime entry point.

    ``run()`` follows the runtime's one startup sequence: open BUS and the
    workspace file tree, read all worker Slots, create each ``BusForWorker``,
    then attach the worker.  ``shutdown()`` performs the
    inverse attachment cleanup.
    """

    def __init__(self) -> None:
        """Open the Runtime BUS from its configured workspace."""
        self.bus = Bus(WORKSPACE_PATH)
        self._workers: dict[str, BaseWorker] = {}

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def run(self) -> bool:
        """Open the configured workers on this runtime's BUS."""
        if self._workers:
            raise ValueError("already running")

        prepared: list[tuple[str, BaseWorker, tuple[SlotTag, ...]]] = []
        for worker_type in WORKERS:
            worker_id = worker_type.worker_name
            if not worker_id:
                raise ValueError(f"{worker_type.__qualname__} needs worker_name")
            prepared.append((worker_id, worker_type(), worker_type.declared_slots()))
        if not prepared:
            raise ValueError("no workers")
        if len({worker_id for worker_id, _, _ in prepared}) != len(prepared):
            raise ValueError("duplicate worker_id")

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

    @staticmethod
    def _detach_workers(workers: dict[str, BaseWorker]) -> None:
        for worker in reversed(tuple(workers.values())):
            worker.detach()
