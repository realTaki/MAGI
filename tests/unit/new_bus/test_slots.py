from __future__ import annotations

from datetime import timedelta

from magi.new_bus import BaseJobResult, Bus, JobStatus, Slot
from magi.new_bus.base.time import utcnow
from tests.unit.new_bus.testing import WORKER, PingJob, PingJobBoard, attach_board


def _attach(bus: Bus, worker_id: str, slots: tuple[str, ...]) -> bool:
    return bus.for_worker(worker_id, tuple(Slot(PingJob, slot) for slot in slots)) is not None


def _board(bus: Bus, worker_id: str, slots: tuple[str, ...]):
    return attach_board(bus, PingJobBoard, worker_id=worker_id, slots=slots)


def _expire(bus: Bus, worker_id: str) -> None:
    bus._heartbeat._until[worker_id] = utcnow() - timedelta(seconds=1)


def test_other_worker_cannot_use_occupied_slot(bus: Bus, ping_board) -> None:
    ping_board.publish(PingJob())
    assert not _attach(bus, "other", ("publish",))


def test_attach_returns_false_for_an_unknown_slot(bus: Bus) -> None:
    assert not _attach(bus, WORKER, ("missing",))


def test_same_worker_reattach_renews(bus: Bus, ping_board) -> None:
    assert _attach(bus, WORKER, ("publish",))
    ping_board.publish(PingJob())


def test_heartbeat_keeps_lease(bus: Bus, ping_board) -> None:
    worker = bus.for_worker(
        WORKER,
        (Slot(PingJob, "publish"), Slot(PingJob, "claim"), Slot(PingJob, "submit_result")),
    )
    assert worker is not None
    assert worker.heartbeat()
    ping_board.publish(PingJob())


def test_expired_lease_can_be_taken(bus: Bus, ping_board) -> None:
    _expire(bus, WORKER)
    other = _board(bus, "other", ("publish", "claim", "submit_result"))
    other.publish(PingJob())


def test_vacant_post_publish_goes_pending(ping_board) -> None:
    job = PingJob()
    job.id = ping_board.publish(job)
    assert ping_board.check_job_status(job.id) is JobStatus.PENDING
    claimed = ping_board.claim()
    assert claimed is not None
    assert claimed.id == job.id


def test_post_publish_then_submit_admits_to_pending(bus: Bus, ping_board) -> None:
    inspector = _board(bus, "inspector", ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = ping_board.publish(job)
    assert ping_board.check_job_status(job.id) is JobStatus.PREPARING
    assert ping_board.claim() is None

    inspected = inspector.post_publish()
    assert inspected is not None
    assert inspected.id == job.id
    assert ping_board.check_job_status(job.id) is JobStatus.HOOKING

    assert inspector.submit_post_publish(inspected, BaseJobResult(status=JobStatus.PENDING))
    assert ping_board.check_job_status(job.id) is JobStatus.PENDING
    claimed = ping_board.claim()
    assert claimed is not None
    assert claimed.id == job.id


def test_submit_post_publish_can_fail_the_job(bus: Bus, ping_board) -> None:
    inspector = _board(bus, "inspector", ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = ping_board.publish(job)
    inspected = inspector.post_publish()
    assert inspected is not None
    assert inspector.submit_post_publish(
        inspected, BaseJobResult(status=JobStatus.FAILED, error="blocked")
    )
    assert ping_board.check_job_status(job.id) is JobStatus.FAILED
    blocked = ping_board.get_result(job.id)
    assert blocked is not None
    assert blocked.error == "blocked"
    assert ping_board.claim() is None


def test_expired_post_publish_slot_releases_preparing(bus: Bus, ping_board) -> None:
    _board(bus, "inspector", ("post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = ping_board.publish(job)
    assert ping_board.check_job_status(job.id) is JobStatus.PREPARING
    _expire(bus, "inspector")
    claimed = ping_board.claim()
    assert claimed is not None
    assert claimed.id == job.id


def test_vacant_post_result_is_readable(ping_board) -> None:
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    outcome = ping_board.get_result(claimed.id)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED


def test_post_result_then_submit_admits_result(bus: Bus, ping_board) -> None:
    hook = _board(bus, "hook", ("post_result", "submit_post_result"))
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    assert ping_board.check_job_status(claimed.id) is JobStatus.SETTLING
    assert ping_board.get_result(claimed.id) is None

    hooked = hook.post_result()
    assert hooked is not None
    assert hooked.id == claimed.id
    assert ping_board.check_job_status(claimed.id) is JobStatus.FINALIZING

    assert hook.submit_post_result(hooked.id, BaseJobResult())
    admitted = ping_board.get_result(claimed.id)
    assert admitted is not None
    assert admitted.status is JobStatus.COMPLETED


def test_submit_post_result_can_fail_the_job(bus: Bus, ping_board) -> None:
    hook = _board(bus, "hook", ("post_result", "submit_post_result"))
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    hooked = hook.post_result()
    assert hooked is not None
    assert hook.submit_post_result(
        hooked.id, BaseJobResult(status=JobStatus.FAILED, error="rejected")
    )
    outcome = ping_board.get_result(claimed.id)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "rejected"


def test_expired_post_result_slot_releases_settling(bus: Bus, ping_board) -> None:
    _board(bus, "hook", ("post_result", "submit_post_result"))
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    assert ping_board.check_job_status(claimed.id) is JobStatus.SETTLING
    _expire(bus, "hook")
    released = ping_board.get_result(claimed.id)
    assert released is not None
    assert released.status is JobStatus.COMPLETED
