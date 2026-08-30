"""Slot declarations and the global runtime that executes them.

``SlotTag`` is only a Worker's declaration. The module-global ``slots`` owns
the runtime for every ``(JobBoard, SlotTag)`` pair.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import wraps
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from .heartbeat import Heartbeat

if TYPE_CHECKING:
    from .BaseJob import BaseJobBoard


@dataclass(frozen=True)
class SlotTag:
    """A Worker's declarative request for one JobBoard operation."""

    job_type: type[Any]
    name: str


class SlotType(StrEnum):
    """Behaviours that are not plain execute-and-optionally-forward."""

    CLAIM_POST = "claim_post"
    SUBMIT_POST = "submit_post"


def slot(
    slot_type: SlotType | None = None,
    *,
    next_slot: str | None = None,
    pass_if_no_worker: Callable[..., Any] | None = None,
):
    """Declare a Slot and dispatch through global ``slots``."""

    def decorate(fn):
        @wraps(fn)
        def wrapped(self, *args, worker_id: str, **kwargs):
            tag = SlotTag(type(self).job_cls, fn.__name__)
            return slots.execute(self, tag, slot_type, next_slot, worker_id, fn, args, kwargs)

        wrapped._slot = True
        wrapped._slot_type = slot_type
        wrapped._slot_next = next_slot
        wrapped._slot_pass_if_no_worker = pass_if_no_worker
        return wrapped

    return decorate


@dataclass
class _Round:
    expected: set[str]
    votes: dict[str, Any]
    submit: Callable[..., Any] | None = None
    args: tuple[Any, ...] | None = None
    kwargs: dict[str, Any] | None = None


class Slot:
    """Membership, lease, optional vacant-pass, and optional next-slot offer."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        self.tag = tag
        self._heartbeat = heartbeat
        self._pass_if_no_worker = pass_if_no_worker
        self._members: set[str] = set()
        self._lock = threading.RLock()

    def attach(self, worker_id: str) -> bool:
        with self._lock:
            self._members.add(worker_id)
        return True

    def holds(self, worker_id: str) -> bool:
        with self._lock:
            self._discard_expired()
            return worker_id in self._members

    def held(self) -> bool:
        with self._lock:
            self._discard_expired()
            return bool(self._members)

    def members(self) -> set[str]:
        with self._lock:
            self._discard_expired()
            return set(self._members)

    def touch(self, worker_id: str) -> bool:
        return self.holds(worker_id) and self._heartbeat.heartbeat(worker_id)

    def execute(
        self,
        board: BaseJobBoard[Any, Any, Any],
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        next_slot: str | None,
        worker_id: str,
    ) -> Any:
        if not self.touch(worker_id):
            return None
        out = fn(board, *args, **kwargs)
        self._offer_next(board, next_slot, self._job_id(out, args))
        return out

    def offer(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> None:
        self.pass_if_unheld(board, job_id)

    def pass_if_unheld(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> bool:
        with self._lock:
            self._discard_expired()
            if self._members or self._pass_if_no_worker is None:
                return False
            pass_if_no_worker = self._pass_if_no_worker
        pass_if_no_worker(board, job_id)
        return True

    def release_vacant(self, board: BaseJobBoard[Any, Any, Any]) -> None:
        with self._lock:
            self._discard_expired()
            if self._members or self._pass_if_no_worker is None:
                return
            pass_if_no_worker = self._pass_if_no_worker
        pass_if_no_worker(board, None)

    def _offer_next(
        self,
        board: BaseJobBoard[Any, Any, Any],
        next_slot: str | None,
        job_id: int | None,
    ) -> None:
        if next_slot is None or job_id is None:
            return
        slots.get(board, SlotTag(self.tag.job_type, next_slot)).offer(board, job_id)

    def _discard_expired(self) -> None:
        self._members.intersection_update(
            worker_id for worker_id in self._members if self._heartbeat.is_alive(worker_id)
        )

    @staticmethod
    def _job_id(out: Any, args: tuple[Any, ...]) -> int | None:
        if out is None or out is False:
            return None
        if isinstance(out, bool):
            ident = getattr(args[0], "id", None) if args else None
            return int(ident) if ident is not None else None
        if isinstance(out, int):
            return out
        ident = getattr(out, "id", None)
        return int(ident) if ident is not None else None


class ClaimPostSlot(Slot):
    """Each Worker sees each waiting Job once, oldest first."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(tag, heartbeat, pass_if_no_worker)
        self._cursor: dict[str, int] = {}

    def attach(self, worker_id: str) -> bool:
        with self._lock:
            super().attach(worker_id)
            self._cursor.setdefault(worker_id, 0)
        return True

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        with self._lock:
            if not self.touch(worker_id):
                return None
            cursor = self._cursor.get(worker_id, 0)
            job = fn(board, *args, **kwargs)
            if job is None or int(job.id) <= cursor:
                return None
            job_id = int(job.id)
            self._cursor[worker_id] = job_id
        self._offer_next(board, next_slot, job_id)
        return job

    def _discard_expired(self) -> None:
        super()._discard_expired()
        live = set(self._members)
        for worker_id in set(self._cursor) - live:
            self._cursor.pop(worker_id, None)


class SubmitPostSlot(Slot):
    """Collect one vote from each live submitter, then persist the aggregate."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(tag, heartbeat, pass_if_no_worker)
        self._rounds: dict[int, _Round] = {}

    def offer(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> None:
        with self._lock:
            self._discard_expired()
            if self._members:
                self._rounds.setdefault(job_id, _Round(expected=self.members(), votes={}))
                return
        self.pass_if_unheld(board, job_id)

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        del next_slot
        if not self.touch(worker_id) or not args:
            return False
        result = args[0]
        job_id = int(result.id)
        with self._lock:
            self._discard_expired()
            round_ = self._rounds.get(job_id)
            if (
                round_ is None
                or worker_id not in round_.expected
                or worker_id in round_.votes
            ):
                return False
            if round_.submit is None:
                round_.submit = fn
                round_.args = args
                round_.kwargs = dict(kwargs)
            round_.votes[worker_id] = result
            settlement = self._take_complete(job_id, round_)
        if settlement is None:
            return True
        return bool(self._persist(board, settlement))

    def release_vacant(self, board: BaseJobBoard[Any, Any, Any]) -> None:
        with self._lock:
            self._discard_expired()
            settlements: list[tuple[Any, Any, tuple[Any, ...], dict[str, Any]]] = []
            job_ids: list[int] = []
            for job_id, round_ in tuple(self._rounds.items()):
                settlement = self._take_complete(job_id, round_)
                if settlement is not None:
                    settlements.append(settlement)
                elif not self._members:
                    self._rounds.pop(job_id, None)
                    job_ids.append(job_id)
        for settlement in settlements:
            self._persist(board, settlement)
        for job_id in job_ids:
            self.pass_if_unheld(board, job_id)

    def _discard_expired(self) -> None:
        super()._discard_expired()
        live = set(self._members)
        for round_ in self._rounds.values():
            round_.expected.intersection_update(live)

    def _take_complete(
        self, job_id: int, round_: _Round
    ) -> tuple[Any, Any, tuple[Any, ...], dict[str, Any]] | None:
        if round_.expected and not round_.expected <= set(round_.votes):
            return None
        if not round_.votes or round_.submit is None or round_.args is None or round_.kwargs is None:
            return None
        failed = [
            vote
            for vote in round_.votes.values()
            if getattr(getattr(vote, "status", None), "value", None) == "failed"
        ]
        result = failed[0] if failed else next(iter(round_.votes.values()))
        if failed:
            errors = [vote.error for vote in failed if vote.error]
            result = replace(result, error="\n".join(errors) or None)
        self._rounds.pop(job_id, None)
        return (round_.submit, result, round_.args, round_.kwargs)

    @staticmethod
    def _persist(
        board: BaseJobBoard[Any, Any, Any],
        settlement: tuple[Any, Any, tuple[Any, ...], dict[str, Any]],
    ) -> Any:
        submit, result, args, kwargs = settlement
        return submit(board, *(*args[:-1], result), **kwargs)


class Slots:
    """Global lookup table used both by ``@slot`` and BUS worker attachment."""

    def __init__(self) -> None:
        self._heartbeats: WeakKeyDictionary[object, Heartbeat] = WeakKeyDictionary()
        self._runtimes: WeakKeyDictionary[object, dict[SlotTag, Slot]] = WeakKeyDictionary()
        self._declarations: dict[SlotTag, SlotType | None] = {}
        self._lock = threading.RLock()

    def register(self, board: BaseJobBoard[Any, Any, Any], heartbeat: Heartbeat) -> None:
        with self._lock:
            self._heartbeats[board] = heartbeat
            self._runtimes.setdefault(board, {})
            for name in dir(type(board)):
                operation = getattr(type(board), name)
                if getattr(operation, "_slot", False):
                    self._declarations[SlotTag(type(board).job_cls, name)] = getattr(
                        operation, "_slot_type", None
                    )

    def has(self, tag: SlotTag) -> bool:
        return tag in self._declarations

    def attach(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag, worker_id: str) -> bool:
        return self.get(board, tag).attach(worker_id)

    def held(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> bool:
        return self.get(board, tag).held()

    def execute(self, board, tag, slot_type, next_slot, worker_id, fn, args, kwargs) -> Any:
        self.release_vacant(board)
        return self.get(board, tag, slot_type).execute(
            board, fn, args, kwargs, next_slot, worker_id
        )

    def release_vacant(self, board: BaseJobBoard[Any, Any, Any]) -> None:
        runtimes = tuple(self._runtimes.get(board, {}).values())
        posts = tuple(runtime for runtime in runtimes if isinstance(runtime, SubmitPostSlot))
        others = tuple(runtime for runtime in runtimes if not isinstance(runtime, SubmitPostSlot))
        for runtime in (*posts, *others):
            runtime.release_vacant(board)

    def get(
        self,
        board: BaseJobBoard[Any, Any, Any],
        tag: SlotTag,
        slot_type: SlotType | None = None,
    ) -> Slot:
        with self._lock:
            runtime = self._runtimes.setdefault(board, {}).get(tag)
            if runtime is not None:
                return runtime
            declared_type = slot_type if slot_type is not None else self._declared_type(board, tag)
            heartbeat = self._heartbeats.get(board)
            if heartbeat is None:
                raise RuntimeError("JobBoard is not registered with slots")
            runtime_cls = {
                SlotType.CLAIM_POST: ClaimPostSlot,
                SlotType.SUBMIT_POST: SubmitPostSlot,
            }.get(declared_type, Slot)
            operation = getattr(type(board), tag.name)
            pass_if_no_worker = getattr(operation, "_slot_pass_if_no_worker", None)
            runtime = runtime_cls(tag, heartbeat, pass_if_no_worker)
            self._runtimes[board][tag] = runtime
            return runtime

    @staticmethod
    def _declared_type(board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> SlotType | None:
        operation = getattr(type(board), tag.name, None)
        slot_type = getattr(operation, "_slot_type", None)
        if slot_type is not None and not isinstance(slot_type, SlotType):
            raise ValueError(f"{tag.name} is not a declared Slot")
        if not getattr(operation, "_slot", False):
            raise ValueError(f"{tag.name} is not a declared Slot")
        return slot_type


slots = Slots()
