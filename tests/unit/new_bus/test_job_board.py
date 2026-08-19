from __future__ import annotations

import threading
from datetime import datetime

import pytest

from magi.new_bus import Bus, InvalidJobError, InvalidJobStateError, JobStatus
from magi.new_bus.base.engine import EngineFactory
from magi.new_bus.testing import PingJob, PingJobBoard


def test_publish_claim_complete(bus: Bus) -> None:
    published = bus.publish(PingJob(n=1, publisher="worker-a"))
    assert published.id
    assert bus.get_result(published) is None
    assert bus.check_job_status(published) is JobStatus.PENDING

    claimed = bus.claim(PingJob)
    assert claimed is not None
    assert claimed.id == published.id
    assert bus.get_result(claimed) is None
    assert bus.check_job_status(claimed) is JobStatus.CLAIMED
    assert claimed.n == 1
    assert isinstance(claimed.created_at, datetime)
    assert claimed.created_at == published.created_at

    done = bus.complete(claimed)
    outcome = bus.get_result(done)
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.id == done.id
    assert bus.get_result(done).status is JobStatus.COMPLETED
    assert not hasattr(done, "result")
    assert not hasattr(done, "status")
    assert not hasattr(done, "error")


def test_job_and_result_share_one_flat_record(bus: Bus) -> None:
    bus.publish(PingJob())
    claimed = bus.claim(PingJob)
    assert claimed is not None
    done = bus.complete(claimed)
    outcome = bus.get_result(done)
    assert outcome.id == done.id
    assert outcome.status is JobStatus.COMPLETED
    assert not hasattr(done, "result")
    assert not hasattr(done, "status")


def test_claim_then_fail(bus: Bus) -> None:
    bus.publish(PingJob())
    claimed = bus.claim(PingJob)
    assert claimed is not None
    failed = bus.fail(claimed, "nope")
    outcome = bus.get_result(failed)
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "nope"


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
    bus.complete(claimed)
    with pytest.raises(InvalidJobStateError):
        bus.complete(claimed)


def test_list_filters_status(bus: Bus) -> None:
    first = bus.publish(PingJob(n=1))
    bus.publish(PingJob(n=2))
    claimed = bus.claim(PingJob)
    assert claimed is not None
    bus.complete(claimed)
    pending = bus.list(PingJob, status=JobStatus.PENDING)
    completed = bus.list(PingJob, status=JobStatus.COMPLETED)
    assert [job.n for job in pending] == [2]
    assert [job.id for job in completed] == [first.id]


def test_claim_is_exclusive(db_backend: EngineFactory) -> None:
    with Bus(db_backend) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        for index in range(20):
            bus.publish(PingJob(n=index))

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


def test_unmounted_job_is_invalid(db_backend: EngineFactory) -> None:
    with Bus(db_backend) as bus:
        with pytest.raises(InvalidJobError):
            bus.publish(PingJob())


def test_book_jobs_cannot_use_mount_job(bus: Bus) -> None:
    from magi.new_bus import ManageBookJob

    with pytest.raises(InvalidJobError):
        bus.mount_job(ManageBookJob)
