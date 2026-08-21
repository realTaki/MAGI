from __future__ import annotations

import threading
from datetime import datetime

import pytest

from magi.new_bus import BaseJobResult, Bus, InvalidJobError, JobStatus
from magi.new_bus.base.engine import EngineFactory
from tests.unit.new_bus.support import WORKER, PingJob, PingJobBoard, occupy


def test_publish_claim_complete(bus: Bus) -> None:
    published = PingJob(n=1, publisher="worker-a")
    published.id = bus.publish(published, worker_id=WORKER)
    assert published.id
    assert bus.get_result(published) is None
    assert bus.check_job_status(published) is JobStatus.PENDING

    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == published.id
    assert bus.get_result(claimed) is None
    assert bus.check_job_status(claimed) is JobStatus.CLAIMED
    assert claimed.n == 1
    assert isinstance(claimed.created_at, datetime)

    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    outcome = bus.get_result(claimed)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.id == claimed.id
    again = bus.get_result(claimed)
    assert again is not None
    assert again.status is JobStatus.COMPLETED
    assert not hasattr(claimed, "result")
    assert not hasattr(claimed, "status")
    assert not hasattr(claimed, "error")


def test_job_and_result_share_one_flat_record(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    outcome = bus.get_result(claimed)
    assert outcome is not None
    assert outcome.id == claimed.id
    assert outcome.status is JobStatus.COMPLETED
    assert not hasattr(claimed, "result")
    assert not hasattr(claimed, "status")


def test_claim_then_fail(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(
        claimed,
        BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="nope"),
        worker_id=WORKER,
    )
    outcome = bus.get_result(claimed)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "nope"


def test_claim_empty_board(bus: Bus) -> None:
    assert bus.claim(PingJob, worker_id=WORKER) is None


def test_illegal_complete_from_pending(bus: Bus) -> None:
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    assert not bus.submit_result(job, BaseJobResult(id=job.id), worker_id=WORKER)


def test_complete_twice_is_illegal(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    assert not bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)


def test_list_filters_status(bus: Bus) -> None:
    first_id = bus.publish(PingJob(n=1), worker_id=WORKER)
    bus.publish(PingJob(n=2), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    pending = bus.list(PingJob, status=JobStatus.PENDING)
    completed = bus.list(PingJob, status=JobStatus.COMPLETED)
    assert [job.n for job in pending] == [2]
    assert [job.id for job in completed] == [first_id]


def test_claim_is_exclusive(db_backend: EngineFactory) -> None:
    with Bus(db_backend) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        occupy(bus)
        for index in range(20):
            bus.publish(PingJob(n=index), worker_id=WORKER)

        claimed_ids: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                job = bus.claim(PingJob, worker_id=WORKER)
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
            bus.publish(PingJob(), worker_id=WORKER)
