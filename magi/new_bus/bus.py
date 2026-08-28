"""Runtime BUS: JobBoards, shared heartbeat, and slot routing."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any, TypeVar, cast

from .base.BaseJob import BaseJob, BaseJobBoard
from .base.dock import AndDock, OrDock
from .base.engine import EngineFactory
from .base.errors import InvalidJobError
from .base.heartbeat import Heartbeat, Slot
from .bus_for_worker import BusForWorker

JobT = TypeVar("JobT", bound=BaseJob)


class Bus:
    """One runtime's source of truth for jobs, slots, docks, and liveness."""

    def __init__(self, factory: EngineFactory) -> None:
        if not isinstance(factory, EngineFactory):
            raise InvalidJobError("Bus requires EngineFactory")
        self._factory = factory
        from .firmware.versions.schema import prepare_schema

        prepare_schema(factory)
        self._heartbeat = Heartbeat()
        from .firmware import create_job_boards

        self._job_boards = create_job_boards(factory, self._heartbeat)
        self._docks: dict[Slot, OrDock | AndDock] = {}
        self._worker_docks: dict[str, set[OrDock | AndDock]] = {}
        self._lock = threading.RLock()

    def for_worker(
        self,
        worker_id: str,
        slots: Iterable[Slot],
    ) -> BusForWorker | None:
        """Allocate Slots and return the shared BUS slice for one Worker."""
        if not self._allocate_worker_slots(worker_id, slots):
            return None
        return BusForWorker(
            bus=self,
            factory=self._factory,
            heartbeat=self._heartbeat,
            worker_docks=self._worker_docks,
            worker_id=worker_id,
        )

    def install_or_dock(self, slot: Slot) -> bool:
        if slot.job_type not in self._job_boards or not self._job_board(slot.job_type).has_slot(
            slot.name
        ):
            return False
        if slot in self._docks:
            return True
        self._docks[slot] = OrDock(self._heartbeat, slot)
        return True

    def install_and_dock(self, slot: Slot) -> bool:
        if slot.job_type not in self._job_boards or not self._job_board(slot.job_type).has_slot(
            slot.name
        ):
            return False
        if slot in self._docks:
            return True
        self._docks[slot] = AndDock(self._heartbeat, slot)
        return True

    def _allocate_worker_slots(self, worker_id: str, slots: Iterable[Slot]) -> bool:
        """Allocate one Worker's declared Slots, routing through Docks when needed."""
        requested = tuple(slots)
        with self._lock:
            if any(
                slot.job_type not in self._job_boards
                or not self._job_board(slot.job_type).has_slot(slot.name)
                for slot in requested
            ):
                return False
            direct = tuple(slot for slot in requested if slot not in self._docks)
            docks = tuple(self._docks[slot] for slot in requested if slot in self._docks)
            if not self._heartbeat.can_attach(worker_id, direct) or not all(
                dock.can_attach() for dock in docks
            ):
                return False
            if not self._heartbeat.attach(worker_id, direct):
                return False
            for dock in docks:
                if not dock.attach(worker_id):
                    return False
                self._worker_docks.setdefault(worker_id, set()).add(dock)
        return True

    def _invoke(self, worker_id: str, job_type: type[JobT], slot_name: str, *args, **kwargs) -> Any:
        slot = Slot(job_type, slot_name)
        board = self._job_board(job_type)
        dock = self._docks.get(slot)
        if dock is not None:
            return dock.call(worker_id, board, *args, **kwargs)
        if not self._heartbeat.holds(worker_id, slot):
            return None
        return getattr(board, slot_name)(*args, worker_id=worker_id, **kwargs)

    def close(self) -> None:
        self._factory.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _job_board(self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        try:
            return cast(BaseJobBoard[JobT, Any, Any], self._job_boards[job_type])
        except KeyError:
            raise InvalidJobError(f"{job_type.__qualname__} is not mounted") from None
