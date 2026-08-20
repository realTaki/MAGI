from __future__ import annotations

from datetime import timedelta

import pytest

from magi.new_bus import BaseJobResult, Bus, InvalidJobError, JobStatus
from magi.new_bus.base.time import utcnow
from magi.new_bus.testing import WORKER, PingJob


def test_other_worker_cannot_use_occupied_slot(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    with pytest.raises(InvalidJobError, match="occupied"):
        bus.attach("other", PingJob, ("publish",))
    with pytest.raises(InvalidJobError, match="not held"):
        bus.claim(PingJob, worker_id="other")


def test_same_worker_reattach_renews(bus: Bus) -> None:
    bus.attach(WORKER, PingJob, ("publish",))
    bus.publish(PingJob(), worker_id=WORKER)


def test_heartbeat_keeps_lease(bus: Bus) -> None:
    bus.heartbeat(WORKER)
    bus.publish(PingJob(), worker_id=WORKER)


def test_expired_lease_can_be_taken(bus: Bus) -> None:
    past = utcnow() - timedelta(seconds=1)
    board = bus.job_board(PingJob)
    for name, holder in list(board._held.items()):
        if holder is not None:
            board._held[name] = (holder[0], past)
    bus.attach("other", PingJob, ("publish", "claim", "submit_result"))
    bus.publish(PingJob(), worker_id="other")


def test_vacant_post_publish_goes_pending(bus: Bus) -> None:
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    assert bus.check_job_status(job) is JobStatus.PENDING
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == job.id


def test_post_publish_then_submit_admits_to_pending(bus: Bus) -> None:
    inspector = "inspector"
    bus.attach(inspector, PingJob, ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    assert bus.check_job_status(job) is JobStatus.PREPARING
    assert bus.claim(PingJob, worker_id=WORKER) is None

    inspected = bus.post_publish(PingJob, worker_id=inspector)
    assert inspected is not None
    assert inspected.id == job.id
    assert bus.check_job_status(job) is JobStatus.HOOKING

    assert bus.submit_post_publish(inspected, BaseJobResult(), worker_id=inspector)
    assert bus.check_job_status(job) is JobStatus.PENDING
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == job.id


def test_submit_post_publish_can_fail_the_job(bus: Bus) -> None:
    inspector = "inspector"
    bus.attach(inspector, PingJob, ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    inspected = bus.post_publish(PingJob, worker_id=inspector)
    assert inspected is not None
    assert bus.submit_post_publish(
        inspected, BaseJobResult(status=JobStatus.FAILED, error="blocked"), worker_id=inspector
    )
    assert bus.check_job_status(job) is JobStatus.FAILED
    assert bus.get_result(job).error == "blocked"
    assert bus.claim(PingJob, worker_id=WORKER) is None


def test_expired_post_publish_slot_releases_preparing(bus: Bus) -> None:
    inspector = "inspector"
    bus.attach(inspector, PingJob, ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    assert bus.check_job_status(job) is JobStatus.PREPARING
    past = utcnow() - timedelta(seconds=1)
    board = bus.job_board(PingJob)
    holder = board._held.get("post_publish")
    assert holder is not None
    board._held["post_publish"] = (holder[0], past)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == job.id
