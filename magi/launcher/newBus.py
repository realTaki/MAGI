"""Plan shared BUS slots before starting workers."""

from __future__ import annotations

from dataclasses import dataclass

from magi.new_bus import Bus, Slot, WorkerBus


@dataclass(frozen=True)
class WorkerSpec[WorkerBusT: WorkerBus]:
    worker_id: str
    bus_type: type[WorkerBusT]


class Launcher:
    """Own one BUS runtime and install default Dock routes before worker start."""

    def __init__(self, bus: Bus) -> None:
        self.bus = bus

    def start[WorkerBusT: WorkerBus](
        self, workers: tuple[WorkerSpec[WorkerBusT], ...]
    ) -> dict[str, WorkerBusT] | None:
        requested: dict[Slot, int] = {}
        for worker in workers:
            for slot in worker.bus_type.declared_slots():
                requested[slot] = requested.get(slot, 0) + 1
        for slot, count in requested.items():
            if count <= 1:
                continue
            install = (
                self.bus.install_and_dock
                if slot.name in {"submit_post_publish", "submit_post_result"}
                else self.bus.install_or_dock
            )
            if not install(slot):
                return None
        started: dict[str, WorkerBusT] = {}
        for worker in workers:
            view = self.bus.for_worker(worker.worker_id, worker.bus_type)
            if not view.attach():
                return None
            started[worker.worker_id] = view
        return started
