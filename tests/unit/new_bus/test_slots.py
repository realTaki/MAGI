from __future__ import annotations

from datetime import timedelta

from magi.new_bus import BaseJobResult, Bus, JobStatus, Slot
from magi.new_bus.base.time import utcnow
from tests.unit.new_bus.support import WORKER, PingJob


def _attach(bus: Bus, worker_id: str, slots: tuple[str, ...]) -> bool:
    return bus.attach(worker_id, tuple(Slot(PingJob, slot) for slot in slots))


def _expire(bus: Bus, worker_id: str) -> None:
    bus._heartbeat._until[worker_id] = utcnow() - timedelta(seconds=1)


def test_other_worker_cannot_use_occupied_slot(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    assert not _attach(bus, "other", ("publish",))
    assert bus.claim(PingJob, worker_id="other") is None


def test_attach_returns_false_for_an_unknown_slot(bus: Bus) -> None:
    assert not _attach(bus, WORKER, ("missing",))


def test_same_worker_reattach_renews(bus: Bus) -> None:
    _attach(bus, WORKER, ("publish",))
    bus.publish(PingJob(), worker_id=WORKER)


def test_heartbeat_keeps_lease(bus: Bus) -> None:
    bus.heartbeat(WORKER)
    bus.publish(PingJob(), worker_id=WORKER)


def test_expired_lease_can_be_taken(bus: Bus) -> None:
    _expire(bus, WORKER)
    _attach(bus, "other", ("publish", "claim", "submit_result"))
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
    _attach(bus, inspector, ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    assert bus.check_job_status(job) is JobStatus.PREPARING
    assert bus.claim(PingJob, worker_id=WORKER) is None

    inspected = bus.post_publish(PingJob, worker_id=inspector)
    assert inspected is not None
    assert inspected.id == job.id
    assert bus.check_job_status(job) is JobStatus.HOOKING

    assert bus.submit_post_publish(
        inspected, BaseJobResult(status=JobStatus.PENDING), worker_id=inspector
    )
    assert bus.check_job_status(job) is JobStatus.PENDING
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == job.id


def test_submit_post_publish_can_fail_the_job(bus: Bus) -> None:
    inspector = "inspector"
    _attach(bus, inspector, ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    inspected = bus.post_publish(PingJob, worker_id=inspector)
    assert inspected is not None
    assert bus.submit_post_publish(
        inspected, BaseJobResult(status=JobStatus.FAILED, error="blocked"), worker_id=inspector
    )
    assert bus.check_job_status(job) is JobStatus.FAILED
    blocked = bus.get_result(job)
    assert blocked is not None
    assert blocked.error == "blocked"
    assert bus.claim(PingJob, worker_id=WORKER) is None


def test_expired_post_publish_slot_releases_preparing(bus: Bus) -> None:
    inspector = "inspector"
    _attach(bus, inspector, ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = bus.publish(job, worker_id=WORKER)
    assert bus.check_job_status(job) is JobStatus.PREPARING
    _expire(bus, inspector)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == job.id


def test_vacant_post_result_is_readable(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    outcome = bus.get_result(claimed)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED


def test_post_result_then_submit_admits_result(bus: Bus) -> None:
    hook = "hook"
    _attach(bus, hook, ("post_result", "submit_post_result"))
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    assert bus.check_job_status(claimed) is JobStatus.SETTLING
    assert bus.get_result(claimed) is None

    hooked = bus.post_result(PingJob, worker_id=hook)
    assert hooked is not None
    assert hooked.id == claimed.id
    assert bus.check_job_status(claimed) is JobStatus.FINALIZING

    assert bus.submit_post_result(hooked, BaseJobResult(), worker_id=hook)
    admitted = bus.get_result(claimed)
    assert admitted is not None
    assert admitted.status is JobStatus.COMPLETED


def test_submit_post_result_can_fail_the_job(bus: Bus) -> None:
    hook = "hook"
    _attach(bus, hook, ("post_result", "submit_post_result"))
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    hooked = bus.post_result(PingJob, worker_id=hook)
    assert hooked is not None
    assert bus.submit_post_result(
        hooked, BaseJobResult(status=JobStatus.FAILED, error="rejected"), worker_id=hook
    )
    outcome = bus.get_result(claimed)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "rejected"


def test_expired_post_result_slot_releases_settling(bus: Bus) -> None:
    hook = "hook"
    _attach(bus, hook, ("post_result", "submit_post_result"))
    bus.publish(PingJob(), worker_id=WORKER)
    claimed = bus.claim(PingJob, worker_id=WORKER)
    assert claimed is not None
    bus.submit_result(claimed, BaseJobResult(id=claimed.id), worker_id=WORKER)
    assert bus.check_job_status(claimed) is JobStatus.SETTLING
    _expire(bus, hook)
    released = bus.get_result(claimed)
    assert released is not None
    assert released.status is JobStatus.COMPLETED
