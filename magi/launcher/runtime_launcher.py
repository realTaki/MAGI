"""Runtime composition root: create BUS, plan Docks, then attach Workers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from magi.new_bus import Bus, EngineFactory, Slot

from .worker import Worker


@dataclass(frozen=True)
class WorkerLaunchSpec:
    """The topology and constructor needed to attach one Worker."""

    worker_id: str
    slots: tuple[Slot, ...]
    create: Callable[[], Worker]


class RuntimeLauncher:
    """The composition root for one Runtime's BUS and Worker topology."""

    def __init__(self, factory: EngineFactory) -> None:
        self.bus = Bus(factory)

    def start(self, specs: tuple[WorkerLaunchSpec, ...]) -> dict[str, Worker] | None:
        """Plan all Docks before allocating slices and attaching Workers."""
        requested: dict[Slot, int] = {}
        for spec in specs:
            for slot in spec.slots:
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

        created = tuple((spec, spec.create()) for spec in specs)
        started: dict[str, Worker] = {}
        for spec, worker in created:
            bus_for_worker = self.bus.for_worker(spec.worker_id, spec.slots)
            if bus_for_worker is None:
                return None
            worker.attach(bus_for_worker)
            started[spec.worker_id] = worker
        return started

    def close(self) -> None:
        self.bus.close()
