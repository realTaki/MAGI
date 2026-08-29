"""Shared worker liveness for one BUS runtime."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from .time import utcnow

LEASE = timedelta(seconds=1)

class Heartbeat:
    """The BUS-private source of Worker liveness."""

    def __init__(self) -> None:
        self._until: dict[str, datetime] = {}
        self._lock = threading.RLock()

    def attach(self, worker_id: str) -> bool:
        now = utcnow()
        with self._lock:
            self._expire(now)
            self._until[worker_id] = now + LEASE
            return True

    def heartbeat(self, worker_id: str) -> bool:
        now = utcnow()
        with self._lock:
            self._expire(now)
            if worker_id not in self._until:
                return False
            self._until[worker_id] = now + LEASE
            return True

    def is_alive(self, worker_id: str) -> bool:
        with self._lock:
            self._expire(utcnow())
            return worker_id in self._until

    def _expire(self, now: datetime) -> None:
        expired = [worker_id for worker_id, until in self._until.items() if until <= now]
        for worker_id in expired:
            self._until.pop(worker_id, None)
