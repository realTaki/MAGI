"""Shared worker liveness and Slot membership for one BUS runtime."""

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
    """The BUS-private source of worker liveness and Slot membership."""

    def __init__(self) -> None:
        self._until: dict[str, datetime] = {}
        self._members: dict[Slot, set[str]] = {}
        self._worker_slots: dict[str, set[Slot]] = {}
        self._lock = threading.RLock()

    def attach(self, worker_id: str, slots: tuple[Slot, ...]) -> bool:
        now = utcnow()
        with self._lock:
            self._expire(now)
            self._until[worker_id] = now + LEASE
            attached = self._worker_slots.setdefault(worker_id, set())
            for slot in slots:
                self._members.setdefault(slot, set()).add(worker_id)
                attached.add(slot)
            return True

    def can_attach(self, worker_id: str, slots: tuple[Slot, ...]) -> bool:
        with self._lock:
            self._expire(utcnow())
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

    def holds(self, worker_id: str, slot: Slot) -> bool:
        with self._lock:
            self._expire(utcnow())
            return worker_id in self._members.get(slot, set())

    def held(self, slot: Slot) -> bool:
        with self._lock:
            self._expire(utcnow())
            return bool(self._members.get(slot))

    def members(self, slot: Slot) -> set[str]:
        """Return the current live Workers attached to *slot*."""
        with self._lock:
            self._expire(utcnow())
            return set(self._members.get(slot, set()))

    def _expire(self, now: datetime) -> None:
        expired = [worker_id for worker_id, until in self._until.items() if until <= now]
        for worker_id in expired:
            self._until.pop(worker_id, None)
            for slot in self._worker_slots.pop(worker_id, set()):
                members = self._members.get(slot)
                if members is None:
                    continue
                members.discard(worker_id)
                if not members:
                    self._members.pop(slot, None)
