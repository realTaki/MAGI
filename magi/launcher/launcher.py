"""Composition root for one MAGI-BUS runtime: topology, then lifecycle."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from magi.new_bus import BaseWorker, Bus, EngineFactory, Slot

_AND_DOCK_SLOTS = frozenset({"submit_post_publish", "submit_post_result"})


@dataclass(frozen=True)
class WorkerSpec:
    """One Worker for Launcher to create and attach."""

    worker_id: str
    worker_type: type[BaseWorker]


class Launcher:
    """Find slots, plug workers in, unplug them to stop.

    BUS provides OrDock / AndDock and Slot ownership. Launcher decides
    topology, then attach/detach is the whole worker lifecycle.
    """

    def __init__(self, database_url: str) -> None:
        """Create the Runtime BUS directly from its database URL."""
        self.bus = Bus(EngineFactory(database_url))
        self._owns_bus = True
        self._workers: dict[str, BaseWorker] = {}

    @classmethod
    def for_bus(cls, bus: Bus) -> Launcher:
        """Build a Launcher around a supplied BUS for focused tests."""
        launcher = cls.__new__(cls)
        launcher.bus = bus
        launcher._owns_bus = False
        launcher._workers = {}
        return launcher

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def attach(self, specs: Sequence[WorkerSpec]) -> dict[str, BaseWorker] | None:
        """Create workers, arrange Docks, then attach each one to a BUS slice."""
        if len({spec.worker_id for spec in specs}) != len(specs):
            raise ValueError("duplicate worker_id")
        prepared = [
            (spec, spec.worker_type(), spec.worker_type.declared_slots())
            for spec in specs
        ]
        if not self._install_docks(slots for _, _, slots in prepared):
            return None

        attached: dict[str, BaseWorker] = {}
        for spec, worker, slots in prepared:
            bus_for_worker = self.bus.for_worker(spec.worker_id, slots)
            if bus_for_worker is None or not worker.attach(bus_for_worker):
                worker.detach()
                self._detach_workers(attached)
                return None
            attached[spec.worker_id] = worker
        self._workers = attached
        return dict(attached)

    def detach(self) -> None:
        self._detach_workers(self._workers)
        self._workers = {}

    def health(self) -> list[dict[str, object]]:
        return [worker.health() for worker in self._workers.values()]

    def __enter__(self) -> Launcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.detach()
        if self._owns_bus:
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


def default_specs() -> tuple[WorkerSpec, ...]:
    """The MAGI-BUS worker set this Launcher currently knows how to assemble."""
    from magi.providers.worker import ProvidersWorker

    return (WorkerSpec(ProvidersWorker.worker_name, ProvidersWorker),)
