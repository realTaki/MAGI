from __future__ import annotations

import pytest

from magi.new_bus import (
    BookOp,
    Bus,
    JobStatus,
    ManageBookJob,
    Slot,
    SlotOccupiedError,
    SlotRejected,
)
from magi.new_bus.base.engine import EngineFactory
from magi.new_bus.testing import ItemBook, PingJob, book_job


def test_single_slot_occupy_detach(bus: Bus) -> None:
    seen: list[str] = []

    def first(job) -> None:
        seen.append(f"a:{job.id}")

    def second(job) -> None:
        seen.append(f"b:{job.id}")

    bus.attach(PingJob, Slot.PRE_PUBLISH, first)
    with pytest.raises(SlotOccupiedError):
        bus.attach(PingJob, Slot.PRE_PUBLISH, second)

    bus.publish(PingJob())
    bus.detach(PingJob, Slot.PRE_PUBLISH, first)
    bus.attach(PingJob, Slot.PRE_PUBLISH, second)
    bus.publish(PingJob())

    assert [item.split(":")[0] for item in seen] == ["a", "b"]


def test_pre_publish_reject_does_not_persist(bus: Bus) -> None:
    def reject(_job) -> None:
        raise SlotRejected("no")

    bus.attach(PingJob, Slot.PRE_PUBLISH, reject)
    with pytest.raises(SlotRejected):
        bus.publish(PingJob())
    assert bus.list(PingJob) == []


def test_pre_claim_reject_leaves_job_pending(bus: Bus) -> None:
    def reject(_job) -> None:
        raise SlotRejected("blocked")

    bus.attach(PingJob, Slot.PRE_CLAIM, reject)
    job = bus.publish(PingJob())
    with pytest.raises(SlotRejected):
        bus.claim(PingJob)
    assert bus.check_job_status(job) is JobStatus.PENDING


def test_multi_publish_fans_out(bus: Bus) -> None:
    received: list[str] = []

    def make(name: str):
        def handler(job) -> None:
            received.append(name)
            if name == "b":
                raise RuntimeError("listener failed")

        return handler

    bus.attach(PingJob, Slot.PUBLISH, make("a"))
    bus.attach(PingJob, Slot.PUBLISH, make("b"))
    bus.attach(PingJob, Slot.PUBLISH, make("c"))
    job = bus.publish(PingJob())

    assert received == ["a", "b", "c"]
    assert bus.check_job_status(job) is JobStatus.PENDING


def test_slots_are_per_job_type(bus: Bus) -> None:
    hits: list[str] = []

    bus.attach(PingJob, Slot.PUBLISH, lambda _job: hits.append("ping"))
    bus.attach(ManageBookJob, Slot.PUBLISH, lambda _job: hits.append("book"))

    bus.publish(PingJob())
    bus.publish(book_job(BookOp.CREATE, name="x"))
    assert hits == ["ping", "book"]


def test_book_job_slots_do_not_deliver_the_work(db_backend: EngineFactory) -> None:
    """Slot handlers observe BaseBook jobs; BUS still executes CRUD itself."""
    observed: list[str] = []
    with Bus(db_backend) as bus:
        bus.mount_book(ItemBook)
        bus.attach(ManageBookJob, Slot.PUBLISH, lambda job: observed.append(job.op.value))
        result = bus.publish(book_job(BookOp.CREATE, name="slot-create"))
        assert bus.get_result(result).status is JobStatus.COMPLETED
        assert observed == ["create"]
        listed = bus.publish(book_job(BookOp.READ))
        assert bus.get_result(listed).records[0]["name"] == "slot-create"
