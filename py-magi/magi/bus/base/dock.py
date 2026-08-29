"""Slot routes that let a group of workers share one Board operation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .BaseJob import BaseJobBoard, BaseJobResult, JobStatus
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

    def can_attach(self) -> bool:
        return self._heartbeat.can_attach(self.dock_id, (self.slot,))

    def heartbeat(self, worker_id: str) -> bool:
        with self._lock:
            if worker_id not in self._members or not self._heartbeat.is_alive(worker_id):
                return False
        return self._heartbeat.heartbeat(self.dock_id)

    def call(self, worker_id: str, board: BaseJobBoard[Any, Any, Any], *args, **kwargs) -> Any:
        if not self.heartbeat(worker_id):
            return None
        return getattr(board, self.slot.name)(*args, worker_id=self.dock_id, **kwargs)


@dataclass
class _AndRound:
    board: BaseJobBoard[Any, Any, Any]
    prefix: tuple[object, ...]
    kwargs: dict[str, object]
    expected: set[str]
    votes: dict[str, BaseJobResult]


class AndDock:
    """One submit slot that waits for all currently live member votes.

    The initial reducer is intentionally conservative: any FAILED result wins;
    otherwise the first successful result is submitted. Domain-specific result
    merging belongs in a later reducer extension.
    """

    def __init__(self, heartbeat: Heartbeat, slot: Slot) -> None:
        self.slot = slot
        self.dock_id = f"dock:and:{slot.job_type.__name__}:{slot.name}"
        self._heartbeat = heartbeat
        self._members: set[str] = set()
        self._rounds: dict[int, _AndRound] = {}
        self._lock = threading.RLock()

    def attach(self, worker_id: str) -> bool:
        with self._lock:
            if not self._heartbeat.attach(self.dock_id, (self.slot,)):
                return False
            self._members.add(worker_id)
            return True

    def can_attach(self) -> bool:
        return self._heartbeat.can_attach(self.dock_id, (self.slot,))

    def heartbeat(self, worker_id: str) -> bool:
        with self._lock:
            if worker_id not in self._members or not self._heartbeat.is_alive(worker_id):
                return False
        if not self._heartbeat.heartbeat(self.dock_id):
            return False
        with self._lock:
            alive = {member for member in self._members if self._heartbeat.is_alive(member)}
            self._settle_ready(alive)
        return True

    def call(self, worker_id: str, board: BaseJobBoard[Any, Any, Any], *args, **kwargs) -> bool:
        if not args or not isinstance(args[-1], BaseJobResult):
            return False
        if not self.heartbeat(worker_id):
            return False
        result = args[-1]
        round_id = int(getattr(args[0], "id", args[0]))
        with self._lock:
            alive = {member for member in self._members if self._heartbeat.is_alive(member)}
            round_ = self._rounds.setdefault(
                round_id,
                _AndRound(board, tuple(args[:-1]), dict(kwargs), alive, {}),
            )
            if worker_id not in round_.expected or worker_id in round_.votes:
                return False
            round_.votes[worker_id] = result
            return self._settle_ready(alive, round_id) or True

    def _settle_ready(self, alive: set[str], only_round: int | None = None) -> bool:
        settled = False
        for round_id, round_ in list(self._rounds.items()):
            if only_round is not None and round_id != only_round:
                continue
            expected = round_.expected & alive
            if not expected or not expected <= set(round_.votes):
                continue
            failed = next(
                (vote for vote in round_.votes.values() if vote.status is JobStatus.FAILED),
                None,
            )
            result = failed or next(iter(round_.votes.values()))
            committed = getattr(round_.board, self.slot.name)(
                *round_.prefix,
                result,
                worker_id=self.dock_id,
                **round_.kwargs,
            )
            settled = bool(committed) or settled
            self._rounds.pop(round_id, None)
        return settled
