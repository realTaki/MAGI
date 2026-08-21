"""Slot routes that let a group of workers share one Board operation."""

from __future__ import annotations

import threading
from typing import Any

from .BaseJob import BaseJobBoard
from .heartbeat import Heartbeat, Slot


class OrDock:
    """One slot owner: any attached live worker may invoke the operation."""

    def __init__(self, heartbeat: Heartbeat, slot: Slot) -> None:
        self.slot = slot
        self.dock_id = f"dock:or:{slot.job_type.__name__}:{slot.name}"
        self._heartbeat = heartbeat
        self._members: set[str] = set()
        self._lock = threading.RLock()

    def attach(self, worker_id: str) -> bool:
        with self._lock:
            if not self._heartbeat.attach(self.dock_id, (self.slot,)):
                return False
            self._members.add(worker_id)
            return True

    def heartbeat(self, worker_id: str) -> bool:
        with self._lock:
            if worker_id not in self._members or not self._heartbeat.is_alive(worker_id):
                return False
        return self._heartbeat.heartbeat(self.dock_id)

    def call(self, worker_id: str, board: BaseJobBoard[Any, Any, Any], *args, **kwargs) -> Any:
        if not self.heartbeat(worker_id):
            return None
        return getattr(board, self.slot.name)(*args, worker_id=self.dock_id, **kwargs)
