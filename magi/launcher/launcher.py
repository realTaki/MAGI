"""Control panel for one MAGI-BUS runtime: launch workers, shut them down."""

from __future__ import annotations

from collections.abc import Iterable

from magi.launcher.constant import DATABASE_URL, WORKERS
from magi.new_bus import BaseWorker, Bus, EngineFactory, Slot

_AND_DOCK_SLOTS = frozenset({"submit_post_publish", "submit_post_result"})


class Launcher:
    """The control panel. It opens the BUS, then launches and stops workers.

    ``launch()`` reads the worker set from ``constant``, seats their Slots,
    and calls ``worker.attach``. ``shutdown()`` calls ``worker.detach``.
    """

    def __init__(self) -> None:
        """Open the Runtime BUS from ``constant.DATABASE_URL``."""
        self.bus = Bus(EngineFactory(DATABASE_URL))
        self._workers: dict[str, BaseWorker] = {}

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def launch(self, *worker_types: type[BaseWorker], **named: type[BaseWorker]) -> bool:
        """Plug in workers. With no arguments, uses ``constant.WORKERS``."""
        if self._workers:
            raise ValueError("already launched")
        if not worker_types and not named:
            worker_types = WORKERS
        items = self._items(*worker_types, **named)
        prepared = [
            (worker_id, worker_type(), worker_type.declared_slots())
            for worker_id, worker_type in items
        ]
        if not self._install_docks(slots for _, _, slots in prepared):
            return False

        launched: dict[str, BaseWorker] = {}
        for worker_id, worker, slots in prepared:
            bus_for_worker = self.bus.for_worker(worker_id, slots)
            if bus_for_worker is None or not worker.attach(bus_for_worker):
                worker.detach()
                self._detach_workers(launched)
                return False
            launched[worker_id] = worker
        self._workers = launched
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
    def _items(
        *worker_types: type[BaseWorker],
        **named: type[BaseWorker],
    ) -> list[tuple[str, type[BaseWorker]]]:
        items: list[tuple[str, type[BaseWorker]]] = []
        for worker_type in worker_types:
            if not worker_type.worker_name:
                raise ValueError(f"{worker_type.__qualname__} needs worker_name")
            items.append((worker_type.worker_name, worker_type))
        items.extend(named.items())
        if not items:
            raise ValueError("no workers")
        ids = [worker_id for worker_id, _ in items]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate worker_id")
        return items

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
