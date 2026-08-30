"""Slot declarations and the global runtime that executes them.

``SlotTag`` is only a Worker's declaration. The module-global ``slots`` owns
the runtime object for every ``(JobBoard, SlotTag)`` pair, so a JobBoard never
needs to implement leases, cursors, or post-hook voting itself.
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
    """Runtime behaviour selected explicitly by ``@slot``."""

    PUBLISH = "publish"
    CLAIM = "claim"
    SUBMIT_RESULT = "submit_result"
    CLAIM_POST = "claim_post"
    SUBMIT_POST = "submit_post"


def slot(
    slot_type: SlotType,
    *,
    next_slot: str | None = None,
    pass_if_no_worker: Callable[..., Any] | None = None,
):
    """Declare an operation's Slot type and dispatch through global ``slots``."""

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


@dataclass(frozen=True)
class PostSettlement:
    job_id: int
    result: Any
    submit: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class PostSubmission:
    accepted: bool
    settlement: PostSettlement | None = None


@dataclass
class _PostJob:
    expected: set[str]
    votes: dict[str, Any]
    submit: Callable[..., Any] | None = None
    args: tuple[Any, ...] | None = None
    kwargs: dict[str, Any] | None = None


class Slot:
    """Base runtime object for one declared operation."""

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
        return bool(self.members())

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
        del next_slot
        if not self.touch(worker_id):
            return None
        return fn(board, *args, **kwargs)

    def pass_if_unheld(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> bool:
        with self._lock:
            self._discard_expired()
            if self._members or self._pass_if_no_worker is None:
                return False
            pass_if_no_worker = self._pass_if_no_worker
        pass_if_no_worker(board, job_id)
        return True

    def _discard_expired(self) -> None:
        self._members.intersection_update(
            worker_id for worker_id in self._members if self._heartbeat.is_alive(worker_id)
        )


class PublishSlot(Slot):
    """Persist a new job, then offer it to an active post-publish gate."""

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        if not self.touch(worker_id):
            return None
        post_tag = SlotTag(self.tag.job_type, next_slot) if next_slot else None
        job_id = fn(board, *args, **kwargs)
        if job_id is not None and post_tag is not None:
            slots.claim_post(board, post_tag).offer(board, int(job_id))
        return job_id


class SubmitResultSlot(Slot):
    """Persist the first result, then offer it to the post-result gate."""

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        if not self.touch(worker_id) or not args:
            return False
        if not fn(board, *args, **kwargs):
            return False
        if next_slot is not None:
            slots.claim_post(board, SlotTag(self.tag.job_type, next_slot)).offer(
                board, int(args[0].id)
            )
        return True


class ClaimPostSlot(Slot):
    """Ordered per-worker ``JobT`` cache for one claim-post operation."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(tag, heartbeat, pass_if_no_worker)
        self._cursor: dict[str, int] = {}
        self._cache: dict[int, Any] = {}
        self._minimum_cursor = 0
        self._maximum_cursor = 0

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
            if cursor != 0 and cursor < self._maximum_cursor:
                job = self._cached_after(cursor)
                if job is None:
                    return None
                self._advance_cursor(worker_id, int(job.id))
                self._offer_to_next(board, next_slot, int(job.id))
                return job
            job = fn(board, *args, **kwargs)
            if job is None or (cursor != 0 and int(job.id) <= cursor):
                return None
            job_id = int(job.id)
            self._remember(job)
            self._advance_cursor(worker_id, job_id)
            self._offer_to_next(board, next_slot, job_id)
            return job

    def offer(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> None:
        with self._lock:
            self._discard_expired()
        self.pass_if_unheld(board, job_id)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._minimum_cursor = 0
            self._maximum_cursor = 0

    def _cached_after(self, cursor: int) -> Any | None:
        candidates = [job_id for job_id in self._cache if job_id > cursor]
        if not candidates:
            return None
        return self._cache[min(candidates)]

    def _offer_to_next(
        self, board: BaseJobBoard[Any, Any, Any], next_slot: str | None, job_id: int
    ) -> None:
        if next_slot is not None:
            slots.submit_post(board, SlotTag(self.tag.job_type, next_slot)).offer(board, job_id)

    def _remember(self, job: Any) -> None:
        job_id = int(job.id)
        self._cache[job_id] = job
        self._maximum_cursor = max(self._maximum_cursor, job_id)

    def _advance_cursor(self, worker_id: str, cursor: int) -> None:
        self._cursor[worker_id] = cursor
        self._refresh_cache_bounds()

    def _refresh_cache_bounds(self) -> None:
        if not self._cursor:
            self._minimum_cursor = 0
            self._cache.clear()
            return
        self._minimum_cursor = min(self._cursor.values())
        for job_id in tuple(self._cache):
            if job_id <= self._minimum_cursor:
                self._cache.pop(job_id, None)

    def _discard_expired(self) -> None:
        super()._discard_expired()
        live = set(self._members)
        for worker_id in set(self._cursor) - live:
            self._cursor.pop(worker_id, None)
        self._refresh_cache_bounds()


class SubmitPostSlot(Slot):
    """Collect all submit-post votes and own their expiry/pass behaviour."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(tag, heartbeat, pass_if_no_worker)
        self._jobs: dict[int, _PostJob] = {}

    def offer(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> None:
        with self._lock:
            self._discard_expired()
            if self._members:
                self._jobs.setdefault(job_id, _PostJob(expected=self.members(), votes={}))
                return
        self.pass_if_unheld(board, job_id)

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        del next_slot
        if not self.touch(worker_id) or not args:
            return False
        result = args[-1]
        job_id = int(result.id) if len(args) == 1 else int(args[0])
        submission = self.submit(worker_id, job_id, result, fn, args, kwargs)
        if not submission.accepted:
            return False
        if submission.settlement is not None:
            return bool(self._submit_aggregate(board, submission.settlement))
        return True

    def submit(
        self,
        worker_id: str,
        job_id: int,
        result: Any,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> PostSubmission:
        with self._lock:
            self._discard_expired()
            round_ = self._jobs.get(job_id)
            if (
                round_ is None
                or worker_id not in round_.expected
                or worker_id in round_.votes
            ):
                return PostSubmission(False)
            if round_.submit is None:
                round_.submit = fn
                round_.args = args
                round_.kwargs = dict(kwargs)
            round_.votes[worker_id] = result
            return PostSubmission(True, self._complete_round(job_id, round_))

    def settle_expired(self, board: BaseJobBoard[Any, Any, Any]) -> None:
        with self._lock:
            self._discard_expired()
            passes: list[int] = []
            settlements: list[PostSettlement] = []
            for job_id, round_ in tuple(self._jobs.items()):
                if not round_.expected and not round_.votes:
                    self._jobs.pop(job_id, None)
                    passes.append(job_id)
                elif (settlement := self._complete_round(job_id, round_)) is not None:
                    settlements.append(settlement)
        for job_id in passes:
            self.pass_if_unheld(board, job_id)
        for settlement in settlements:
            self._submit_aggregate(board, settlement)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _discard_expired(self) -> None:
        super()._discard_expired()
        live = set(self._members)
        for round_ in self._jobs.values():
            round_.expected.intersection_update(live)

    def _complete_round(self, job_id: int, round_: _PostJob) -> PostSettlement | None:
        if round_.expected and not round_.expected <= set(round_.votes):
            return None
        if not round_.votes:
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
        self._jobs.pop(job_id, None)
        if round_.submit is None or round_.args is None or round_.kwargs is None:
            return None
        return PostSettlement(job_id, result, round_.submit, round_.args, round_.kwargs)

    def _submit_aggregate(
        self, board: BaseJobBoard[Any, Any, Any], settlement: PostSettlement
    ) -> Any:
        args = (*settlement.args[:-1], settlement.result)
        return settlement.submit(
            board, *args, **settlement.kwargs
        )

class Slots:
    """Global lookup table used both by ``@slot`` and BUS worker attachment."""

    def __init__(self) -> None:
        self._heartbeats: WeakKeyDictionary[object, Heartbeat] = WeakKeyDictionary()
        self._runtimes: WeakKeyDictionary[object, dict[SlotTag, Slot]] = WeakKeyDictionary()
        self._declarations: dict[SlotTag, SlotType] = {}
        self._lock = threading.RLock()

    def register(self, board: BaseJobBoard[Any, Any, Any], heartbeat: Heartbeat) -> None:
        with self._lock:
            self._heartbeats[board] = heartbeat
            self._runtimes.setdefault(board, {})
            for name in dir(type(board)):
                operation = getattr(type(board), name)
                slot_type = getattr(operation, "_slot_type", None)
                if isinstance(slot_type, SlotType):
                    self._declarations[SlotTag(type(board).job_cls, name)] = slot_type

    def has(self, tag: SlotTag) -> bool:
        return tag in self._declarations

    def attach(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag, worker_id: str) -> bool:
        return self.get(board, tag).attach(worker_id)

    def holds(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag, worker_id: str) -> bool:
        return self.get(board, tag).holds(worker_id)

    def held(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> bool:
        return self.get(board, tag).held()

    def claim_post(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> ClaimPostSlot:
        runtime = self.get(board, tag)
        if not isinstance(runtime, ClaimPostSlot):
            raise ValueError(f"{tag.name} is not a claim-post Slot")
        return runtime

    def submit_post(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> SubmitPostSlot:
        runtime = self.get(board, tag)
        if not isinstance(runtime, SubmitPostSlot):
            raise ValueError(f"{tag.name} is not a submit-post Slot")
        return runtime

    def execute(self, board, tag, slot_type, next_slot, worker_id, fn, args, kwargs) -> Any:
        runtime = self.get(board, tag, slot_type)
        return runtime.execute(board, fn, args, kwargs, next_slot, worker_id)

    def settle_expired(
        self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag
    ) -> None:
        self.submit_post(board, tag).settle_expired(board)

    def clear(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> None:
        self.claim_post(board, tag).clear()

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
            declared_type = slot_type or self._declared_type(board, tag)
            heartbeat = self._heartbeats.get(board)
            if heartbeat is None:
                raise RuntimeError("JobBoard is not registered with slots")
            runtime_cls = {
                SlotType.PUBLISH: PublishSlot,
                SlotType.SUBMIT_RESULT: SubmitResultSlot,
                SlotType.CLAIM_POST: ClaimPostSlot,
                SlotType.SUBMIT_POST: SubmitPostSlot,
            }.get(declared_type, Slot)
            operation = getattr(type(board), tag.name)
            pass_if_no_worker = getattr(operation, "_slot_pass_if_no_worker", None)
            runtime = runtime_cls(tag, heartbeat, pass_if_no_worker)
            self._runtimes[board][tag] = runtime
            return runtime

    @staticmethod
    def _declared_type(board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> SlotType:
        operation = getattr(type(board), tag.name, None)
        slot_type = getattr(operation, "_slot_type", None)
        if not isinstance(slot_type, SlotType):
            raise ValueError(f"{tag.name} is not a declared Slot")
        return slot_type


slots = Slots()
