from __future__ import annotations

import threading

import pytest

from magi.new_bus import Bus, InvalidJobError, InvalidJobStateError, JobStatus
from magi.new_bus.base.backend import Backend
from magi.new_bus.testing import PingJob


def test_publish_claim_complete(bus: Bus) -> None:
    published = bus.publish(PingJob(payload={"n": 1}, publisher="worker-a"))
    assert published.id
    assert published.status is JobStatus.PENDING

    claimed = bus.claim(PingJob)
    assert claimed is not None
    assert claimed.id == published.id
    assert claimed.status is JobStatus.CLAIMED
    assert claimed.payload["n"] == 1

    done = bus.complete(claimed, result={"ok": True})
    assert done.status is JobStatus.COMPLETED
    assert done.result == {"ok": True}
    assert bus.get(PingJob, done.id).status is JobStatus.COMPLETED


def test_claim_then_fail(bus: Bus) -> None:
    bus.publish(PingJob())
    claimed = bus.claim(PingJob)
    assert claimed is not None
    failed = bus.fail(claimed, "nope")
    assert failed.status is JobStatus.FAILED
    assert failed.error == "nope"


def test_claim_empty_board(bus: Bus) -> None:
    assert bus.claim(PingJob) is None


def test_illegal_complete_from_pending(bus: Bus) -> None:
    job = bus.publish(PingJob())
    with pytest.raises(InvalidJobStateError):
        bus.complete(job, result=1)


def test_complete_twice_is_illegal(bus: Bus) -> None:
    bus.publish(PingJob())
    claimed = bus.claim(PingJob)
    assert claimed is not None
    bus.complete(claimed, result=1)
    with pytest.raises(InvalidJobStateError):
        bus.complete(claimed, result=2)


def test_list_filters_status(bus: Bus) -> None:
    first = bus.publish(PingJob(payload={"i": 1}))
    bus.publish(PingJob(payload={"i": 2}))
    claimed = bus.claim(PingJob)
    assert claimed is not None
    bus.complete(claimed)
    pending = bus.list(PingJob, status=JobStatus.PENDING)
    completed = bus.list(PingJob, status=JobStatus.COMPLETED)
    assert [job.payload["i"] for job in pending] == [2]
    assert [job.id for job in completed] == [first.id]


def test_claim_is_exclusive(backend: Backend) -> None:
    with Bus(backend) as bus:
        bus.mount_job(PingJob)
        for index in range(20):
            bus.publish(PingJob(payload={"i": index}))

        claimed_ids: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                job = bus.claim(PingJob)
                if job is None:
                    return
                with lock:
                    claimed_ids.append(job.id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(claimed_ids) == 20
        assert len(set(claimed_ids)) == 20


def test_unmounted_job_is_invalid(backend: Backend) -> None:
    with Bus(backend) as bus:
        with pytest.raises(InvalidJobError):
            bus.publish(PingJob())


def test_book_jobs_cannot_use_mount_job(bus: Bus) -> None:
    from magi.new_bus import ManageBookJob

    with pytest.raises(InvalidJobError):
        bus.mount_job(ManageBookJob)
