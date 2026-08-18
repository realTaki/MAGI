"""Slot is a BaseJob-type lifecycle feature, not a work-delivery mechanism."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from .errors import SlotNotFoundError, SlotOccupiedError
from .job import BaseJob

Handler = Callable[[BaseJob], None]


class Slot(StrEnum):
    PRE_PUBLISH = "pre_publish"
    PUBLISH = "publish"
    POST_PUBLISH = "post_publish"
    PRE_CLAIM = "pre_claim"
    CLAIM = "claim"
    POST_CLAIM = "post_claim"


SINGLE_SLOTS = frozenset(
    {
        Slot.PRE_PUBLISH,
        Slot.POST_PUBLISH,
        Slot.PRE_CLAIM,
        Slot.CLAIM,
        Slot.POST_CLAIM,
    }
)
MULTI_SLOTS = frozenset({Slot.PUBLISH})


class SlotSpace:
    """Per-Bus bindings keyed by BaseJob type.

    Workers still pull :meth:`BaseJobBoard.claim`. Bindings only intercept
    or observe those lifecycle points.
    """

    def __init__(self) -> None:
        self._single: dict[tuple[type[BaseJob], Slot], Handler] = {}
        self._multi: dict[tuple[type[BaseJob], Slot], list[Handler]] = {}

    def attach(self, job_type: type[BaseJob], slot: Slot, handler: Handler) -> None:
        slot = _require_slot(slot)
        key = (job_type, slot)
        if slot in MULTI_SLOTS:
            handlers = self._multi.setdefault(key, [])
            if handler not in handlers:
                handlers.append(handler)
            return
        existing = self._single.get(key)
        if existing is not None and existing is not handler:
            raise SlotOccupiedError(f"{job_type.type_name()}.{slot} is occupied")
        self._single[key] = handler

    def detach(self, job_type: type[BaseJob], slot: Slot, handler: Handler) -> None:
        slot = _require_slot(slot)
        key = (job_type, slot)
        if slot in MULTI_SLOTS:
            handlers = self._multi.get(key)
            if not handlers or handler not in handlers:
                raise SlotNotFoundError(f"{job_type.type_name()}.{slot} has no such handler")
            handlers.remove(handler)
            if not handlers:
                self._multi.pop(key, None)
            return
        existing = self._single.get(key)
        if existing is not handler:
            raise SlotNotFoundError(f"{job_type.type_name()}.{slot} is not bound to this handler")
        del self._single[key]

    def fire(self, job_type: type[BaseJob], slot: Slot, job: BaseJob) -> None:
        slot = _require_slot(slot)
        key = (job_type, slot)
        if slot in MULTI_SLOTS:
            for handler in list(self._multi.get(key, ())):
                try:
                    handler(job)
                except Exception:
                    # Listeners are independent: one failure must not hide the rest.
                    continue
            return
        handler = self._single.get(key)
        if handler is not None:
            handler(job)


def _require_slot(slot: Slot | str) -> Slot:
    try:
        return Slot(slot)
    except ValueError as exc:
        raise SlotNotFoundError(f"unknown slot {slot!r}") from exc
