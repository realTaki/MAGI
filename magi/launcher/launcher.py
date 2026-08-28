"""Composition root for one MAGI-BUS runtime: topology, then lifecycle."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from magi.new_bus import BaseWorker, Bus, Slot

_AND_DOCK_SLOTS = frozenset({"submit_post_publish", "submit_post_result"})


@dataclass(frozen=True)
class WorkerSpec:
    """One Worker to create, attach, and start."""

    worker_id: str
    worker_type: type[BaseWorker]


class Launcher:
    """Plan Docks, attach workers to BUS slices, and own their lifecycle.

    BUS provides OrDock / AndDock mechanism and Slot ownership. This class
    decides topology (when to install which Dock) and when workers run.
    """

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self._workers: dict[str, BaseWorker] = {}

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def start(self, specs: Sequence[WorkerSpec]) -> dict[str, BaseWorker] | None:
        """Install Docks, attach slices, then start workers. All or nothing."""
        if len({spec.worker_id for spec in specs}) != len(specs):
            raise ValueError("duplicate worker_id")
        if not self._install_docks(specs):
            return None

        started: dict[str, BaseWorker] = {}
        for spec in specs:
            worker = spec.worker_type()
            bus_for_worker = self.bus.for_worker(spec.worker_id, spec.worker_type.declared_slots())
            if bus_for_worker is None:
                self._stop_workers(started)
                return None
            worker.attach(bus_for_worker)
            if not worker.start():
                worker.stop()
                self._stop_workers(started)
                return None
            started[spec.worker_id] = worker
        self._workers = started
        return dict(started)

    def stop(self) -> None:
        self._stop_workers(self._workers)
        self._workers = {}

    def health(self) -> list[dict[str, object]]:
        return [worker.health() for worker in self._workers.values()]

    def __enter__(self) -> Launcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _install_docks(self, specs: Sequence[WorkerSpec]) -> bool:
        requested: dict[Slot, int] = {}
        for spec in specs:
            for slot in spec.worker_type.declared_slots():
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
    def _stop_workers(workers: dict[str, BaseWorker]) -> None:
        for worker in reversed(tuple(workers.values())):
            worker.stop()
