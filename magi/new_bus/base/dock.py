"""Composable owners for one shared JobBoard slot."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from .BaseJob import LEASE, BaseJob
from .time import BaseTime, utcnow

if TYPE_CHECKING:
    from ..bus import Bus


class OrDock:
    """Share one attached slot between multiple leased workers.

    The Dock is the sole BUS-visible slot owner. A worker attaches to the Dock,
    then calls the one configured Board operation through ``call()``. The
    JobBoard remains responsible for the operation's atomic state transition.
    """

    def __init__(self, slot: str, *, name: str | None = None) -> None:
        self.slot = slot
        self.dock_id = f"dock:{name or slot}:{slot}"
        self._bus: Bus | None = None
        self._job_type: type[BaseJob] | None = None
        self._workers: dict[str, BaseTime] = {}
        self._lock = threading.RLock()

    def attach[JobT: BaseJob](self, bus: Bus, worker_id: str, job_type: type[JobT]) -> bool:
        """Attach a worker and acquire this Dock's one slot."""
        now = utcnow()
        with self._lock:
            if self._bus is not None and self._bus is not bus:
                return False
            if self._job_type is not None and self._job_type is not job_type:
                return False
            if job_type not in bus.jobs:
                return False
            if not bus.attach(self.dock_id, job_type, (self.slot,)):
                return False
            self._bus = bus
            self._job_type = job_type
            self._workers[worker_id] = now + LEASE
            return True

    def heartbeat(self, worker_id: str) -> bool:
        """Renew one attached worker and this Dock's single slot lease."""
        now = utcnow()
        with self._lock:
            bus = self._bus
            until = self._workers.get(worker_id)
            if bus is None or until is None or until <= now:
                return False
            self._workers[worker_id] = now + LEASE
        bus.heartbeat(self.dock_id)
        return True

    def call(self, worker_id: str, *args: object, **kwargs: object) -> Any:
        """Invoke this Dock's one Board slot as its virtual worker."""
        now = utcnow()
        with self._lock:
            bus = self._bus
            job_type = self._job_type
            until = self._workers.get(worker_id)
            if bus is None or job_type is None or until is None or until <= now:
                return None
            self._workers[worker_id] = now + LEASE
        bus.heartbeat(self.dock_id)
        operation = getattr(bus.job_board(job_type), self.slot, None)
        if operation is None:
            return None
        return operation(*args, worker_id=self.dock_id, **kwargs)
