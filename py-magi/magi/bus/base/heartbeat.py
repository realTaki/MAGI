"""Shared worker liveness and slot ownership for one BUS runtime."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .time import utcnow

LEASE = timedelta(seconds=1)


@dataclass(frozen=True)
class Slot:
    """One named operation on one Job type."""

    job_type: type[Any]
    name: str


class Heartbeat:
    """The BUS-private source of worker liveness and slot ownership."""

    def __init__(self) -> None:
        self._until: dict[str, datetime] = {}
        self._owners: dict[Slot, str] = {}
        self._worker_slots: dict[str, set[Slot]] = {}
        self._lock = threading.RLock()

    def attach(self, worker_id: str, slots: tuple[Slot, ...]) -> bool:
        now = utcnow()
        with self._lock:
            self._expire(now)
            if not self._can_attach(worker_id, slots):
                return False
            self._until[worker_id] = now + LEASE
            owned = self._worker_slots.setdefault(worker_id, set())
            for slot in slots:
                self._owners[slot] = worker_id
                owned.add(slot)
            return True

    def can_attach(self, worker_id: str, slots: tuple[Slot, ...]) -> bool:
        with self._lock:
            self._expire(utcnow())
            return self._can_attach(worker_id, slots)

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

    def holds(self, worker_id: str, slot: Slot) -> bool:
        with self._lock:
            self._expire(utcnow())
            return self._owners.get(slot) == worker_id

    def held(self, slot: Slot) -> bool:
        with self._lock:
            self._expire(utcnow())
            return slot in self._owners

    def _expire(self, now: datetime) -> None:
        expired = [worker_id for worker_id, until in self._until.items() if until <= now]
        for worker_id in expired:
            self._until.pop(worker_id, None)
            for slot in self._worker_slots.pop(worker_id, set()):
                if self._owners.get(slot) == worker_id:
                    self._owners.pop(slot, None)

    def _can_attach(self, worker_id: str, slots: tuple[Slot, ...]) -> bool:
        return not any(
            (owner := self._owners.get(slot)) is not None and owner != worker_id for slot in slots
        )
