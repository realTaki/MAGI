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


@dataclass(frozen=True)
class PostSubmission:
    accepted: bool
    settlement: PostSettlement | None = None


@dataclass
class _PostJob:
    expected: set[str]
    votes: dict[str, Any]


class Slot:
    """Base runtime object for one declared operation."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        del pass_if_no_worker
        self.tag = tag
        self._heartbeat = heartbeat
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
            slots.post(board, post_tag).offer(board, int(job_id))
        return job_id


class SubmitResultSlot(Slot):
    """First durable result wins; an active post-result gate gets the row."""

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        if not self.touch(worker_id):
            return None
        post_active = bool(
            next_slot and slots.held(board, SlotTag(self.tag.job_type, next_slot))
        )
        return fn(board, *args, _slot_post_active=post_active, **kwargs)


class ClaimPostSlot(Slot):
    """Ordered per-worker post stream plus the all-member vote cache."""

    def __init__(
        self,
        tag: SlotTag,
        heartbeat: Heartbeat,
        pass_if_no_worker: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(tag, heartbeat)
        self._pass_if_no_worker = pass_if_no_worker
        self._cursor: dict[str, int] = {}
        self._jobs: dict[int, _PostJob] = {}
        self._high_watermark = 0

    def attach(self, worker_id: str) -> bool:
        with self._lock:
            super().attach(worker_id)
            self._cursor.setdefault(worker_id, self._high_watermark)
        return True

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        del next_slot
        with self._lock:
            if not self.touch(worker_id):
                return None
            cursor = self._cursor.get(worker_id, 0)
            job = fn(board, *args, _slot_cursor=cursor, **kwargs)
            if job is None:
                return None
            job_id = int(job.id)
            self._high_watermark = max(self._high_watermark, job_id)
            self._jobs.setdefault(job_id, _PostJob(expected=self.members(), votes={}))
            self._cursor[worker_id] = job_id
            return job

    def offer(self, board: BaseJobBoard[Any, Any, Any], job_id: int) -> None:
        with self._lock:
            self._discard_expired()
            self._high_watermark = max(self._high_watermark, job_id)
            if not self._members:
                pass_if_no_worker = self._pass_if_no_worker
            else:
                pass_if_no_worker = None
                self._jobs.setdefault(job_id, _PostJob(expected=self.members(), votes={}))
        if pass_if_no_worker is not None:
            pass_if_no_worker(board, job_id)

    def submit(self, worker_id: str, job_id: int, result: Any) -> PostSubmission:
        with self._lock:
            self._discard_expired()
            round_ = self._jobs.get(job_id)
            if (
                round_ is None
                or self._cursor.get(worker_id, 0) < job_id
                or worker_id not in round_.expected
                or worker_id in round_.votes
            ):
                return PostSubmission(False)
            round_.votes[worker_id] = result
            return PostSubmission(True, self._settle(job_id, round_))

    def settle_expired(self) -> list[PostSettlement]:
        with self._lock:
            self._discard_expired()
            return [
                settlement
                for job_id, round_ in tuple(self._jobs.items())
                if (settlement := self._settle(job_id, round_)) is not None
            ]

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _discard_expired(self) -> None:
        super()._discard_expired()
        live = set(self._members)
        for worker_id in set(self._cursor) - live:
            self._cursor.pop(worker_id, None)
        for round_ in self._jobs.values():
            round_.expected.intersection_update(live)

    def _settle(self, job_id: int, round_: _PostJob) -> PostSettlement | None:
        if not round_.expected or not round_.expected <= set(round_.votes):
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
        return PostSettlement(job_id, result)


class PostSubmitSlot(Slot):
    """Vote through the paired post-claim Slot; only the final vote settles."""

    def execute(
        self, board, fn, args, kwargs, next_slot, worker_id: str
    ) -> Any:
        del next_slot
        if not self.touch(worker_id) or len(args) < 2:
            return False
        if not fn(board, *args, _slot_settlement=None, **kwargs):
            return False
        job_or_id, result = args[:2]
        job_id = int(getattr(job_or_id, "id", job_or_id))
        claim_tag = SlotTag(
            self.tag.job_type,
            f"claim_{self.tag.name.removeprefix('submit_')}",
        )
        submission = slots.post(board, claim_tag).submit(worker_id, job_id, result)
        if not submission.accepted:
            return False
        if submission.settlement is not None:
            fn(board, *args, _slot_settlement=submission.settlement, **kwargs)
        return True

class Slots:
    """Global lookup table used both by ``@slot`` and BUS worker attachment."""

    def __init__(self) -> None:
        self._heartbeats: WeakKeyDictionary[object, Heartbeat] = WeakKeyDictionary()
        self._runtimes: WeakKeyDictionary[object, dict[SlotTag, Slot]] = WeakKeyDictionary()
        self._lock = threading.RLock()

    def register(self, board: BaseJobBoard[Any, Any, Any], heartbeat: Heartbeat) -> None:
        with self._lock:
            self._heartbeats[board] = heartbeat
            self._runtimes.setdefault(board, {})

    def attach(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag, worker_id: str) -> bool:
        return self.get(board, tag).attach(worker_id)

    def holds(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag, worker_id: str) -> bool:
        return self.get(board, tag).holds(worker_id)

    def held(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> bool:
        return self.get(board, tag).held()

    def post(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> ClaimPostSlot:
        runtime = self.get(board, tag)
        if not isinstance(runtime, ClaimPostSlot):
            raise ValueError(f"{tag.name} is not a claim-post Slot")
        return runtime

    def execute(self, board, tag, slot_type, next_slot, worker_id, fn, args, kwargs) -> Any:
        runtime = self.get(board, tag, slot_type)
        return runtime.execute(board, fn, args, kwargs, next_slot, worker_id)

    def settle_expired(
        self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag
    ) -> list[PostSettlement]:
        return self.post(board, tag).settle_expired()

    def clear(self, board: BaseJobBoard[Any, Any, Any], tag: SlotTag) -> None:
        self.post(board, tag).clear()

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
                SlotType.SUBMIT_POST: PostSubmitSlot,
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
