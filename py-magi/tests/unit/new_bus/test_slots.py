from __future__ import annotations

from datetime import timedelta

from bus import BaseJobResult, Bus, JobStatus, SlotTag
from bus.base.time import utcnow
from tests.unit.new_bus.testing import WORKER, PingJob, PingJobBoard, attach_board


def _attach(bus: Bus, worker_id: str, slots: tuple[str, ...]) -> bool:
    return bus.for_worker(worker_id, tuple(SlotTag(PingJob, slot) for slot in slots)) is not None


def _board(bus: Bus, worker_id: str, slots: tuple[str, ...]):
    return attach_board(bus, PingJobBoard, worker_id=worker_id, slots=slots)


def _expire(bus: Bus, worker_id: str) -> None:
    bus._heartbeat._until[worker_id] = utcnow() - timedelta(seconds=1)


def test_multiple_workers_can_attach_and_use_one_slot(bus: Bus, ping_board) -> None:
    other = _board(bus, "other", ("publish",))
    assert ping_board.publish(PingJob())
    assert other.publish(PingJob())


def test_attach_returns_false_for_an_unknown_slot(bus: Bus) -> None:
    assert not _attach(bus, WORKER, ("missing",))


def test_same_worker_reattach_renews(bus: Bus, ping_board) -> None:
    assert _attach(bus, WORKER, ("publish",))
    ping_board.publish(PingJob())


def test_heartbeat_keeps_lease(bus: Bus, ping_board) -> None:
    worker = bus.for_worker(
        WORKER,
        (
            SlotTag(PingJob, "publish"),
            SlotTag(PingJob, "claim"),
            SlotTag(PingJob, "submit_result"),
        ),
    )
    assert worker is not None
    assert worker.heartbeat()
    ping_board.publish(PingJob())


def test_expired_lease_removes_only_that_worker(bus: Bus, ping_board) -> None:
    other = _board(bus, "other", ("publish",))
    _expire(bus, WORKER)
    assert other.publish(PingJob())


def test_vacant_post_publish_goes_pending(ping_board) -> None:
    job = PingJob()
    job.id = ping_board.publish(job)
    assert ping_board.check_job_status(job.id) is JobStatus.PENDING
    claimed = ping_board.claim()
    assert claimed is not None
    assert claimed.id == job.id


def test_claim_post_publish_is_shared_and_all_submitters_must_vote(bus: Bus, ping_board) -> None:
    first = _board(bus, "first", ("claim_post_publish", "submit_post_publish"))
    second = _board(bus, "second", ("claim_post_publish", "submit_post_publish"))
    job = PingJob()
    job.id = ping_board.publish(job)

    first_claim = first.claim_post_publish()
    second_claim = second.claim_post_publish()
    assert first_claim is not None and second_claim is not None
    assert first_claim.id == second_claim.id == job.id
    assert ping_board.check_job_status(job.id) is JobStatus.PREPARING

    assert first.submit_post_publish(first_claim, BaseJobResult())
    assert ping_board.check_job_status(job.id) is JobStatus.PREPARING
    assert second.submit_post_publish(second_claim, BaseJobResult())
    assert ping_board.check_job_status(job.id) is JobStatus.PENDING


def test_post_publish_failure_merges_errors_from_all_workers(bus: Bus, ping_board) -> None:
    first = _board(bus, "first", ("claim_post_publish", "submit_post_publish"))
    second = _board(bus, "second", ("claim_post_publish", "submit_post_publish"))
    job = PingJob(id=ping_board.publish(PingJob()))
    first_claim = first.claim_post_publish()
    second_claim = second.claim_post_publish()
    assert first_claim is not None and second_claim is not None
    assert first.submit_post_publish(
        first_claim, BaseJobResult(status=JobStatus.FAILED, error="first block")
    )
    assert second.submit_post_publish(
        second_claim, BaseJobResult(status=JobStatus.FAILED, error="second block")
    )
    outcome = ping_board.get_result(job.id)
    assert outcome is not None and outcome.status is JobStatus.FAILED
    assert outcome.error == "first block\nsecond block"


def test_expired_post_publish_workers_release_preparing(bus: Bus, ping_board) -> None:
    _board(bus, "inspector", ("claim_post_publish", "submit_post_publish"))
    job = PingJob(id=ping_board.publish(PingJob()))
    _expire(bus, "inspector")
    claimed = ping_board.claim()
    assert claimed is not None and claimed.id == job.id


def test_claim_is_first_claimant_wins(bus: Bus, ping_board) -> None:
    other = _board(bus, "other", ("claim",))
    ping_board.publish(PingJob())
    assert ping_board.claim() is not None
    assert other.claim() is None


def test_duplicate_submit_result_does_not_replace_the_first_result(bus: Bus, ping_board) -> None:
    other = _board(bus, "other", ("submit_result",))
    job = PingJob(id=ping_board.publish(PingJob()))
    claimed = ping_board.claim()
    assert claimed is not None
    assert ping_board.submit_result(BaseJobResult(id=job.id, error="first"))
    assert not other.submit_result(BaseJobResult(id=job.id, status=JobStatus.FAILED, error="late"))
    outcome = ping_board.get_result(job.id)
    assert outcome is not None and outcome.status is JobStatus.COMPLETED
    assert outcome.error == "first"


def test_claim_post_result_is_shared_and_all_submitters_must_vote(bus: Bus, ping_board) -> None:
    first = _board(bus, "first", ("claim_post_result", "submit_post_result"))
    second = _board(bus, "second", ("claim_post_result", "submit_post_result"))
    job = PingJob(id=ping_board.publish(PingJob()))
    claimed = ping_board.claim()
    assert claimed is not None
    assert ping_board.submit_result(BaseJobResult(id=job.id))

    first_claim = first.claim_post_result()
    second_claim = second.claim_post_result()
    assert first_claim is not None and second_claim is not None
    assert first_claim.id == second_claim.id == job.id
    assert first.submit_post_result(job.id, BaseJobResult())
    assert ping_board.get_result(job.id) is None
    assert second.submit_post_result(job.id, BaseJobResult())
    outcome = ping_board.get_result(job.id)
    assert outcome is not None and outcome.status is JobStatus.COMPLETED


def test_post_result_failure_merges_errors_from_all_workers(bus: Bus, ping_board) -> None:
    first = _board(bus, "first", ("claim_post_result", "submit_post_result"))
    second = _board(bus, "second", ("claim_post_result", "submit_post_result"))
    job = PingJob(id=ping_board.publish(PingJob()))
    claimed = ping_board.claim()
    assert claimed is not None
    assert ping_board.submit_result(BaseJobResult(id=job.id))
    assert first.submit_post_result(job.id, BaseJobResult(status=JobStatus.FAILED, error="first reject"))
    assert second.submit_post_result(job.id, BaseJobResult(status=JobStatus.FAILED, error="second reject"))
    outcome = ping_board.get_result(job.id)
    assert outcome is not None and outcome.status is JobStatus.FAILED
    assert outcome.error == "first reject\nsecond reject"


def test_expired_post_result_workers_release_settling(bus: Bus, ping_board) -> None:
    _board(bus, "hook", ("claim_post_result", "submit_post_result"))
    job = PingJob(id=ping_board.publish(PingJob()))
    claimed = ping_board.claim()
    assert claimed is not None
    assert ping_board.submit_result(BaseJobResult(id=job.id))
    _expire(bus, "hook")
    outcome = ping_board.get_result(job.id)
    assert outcome is not None and outcome.status is JobStatus.COMPLETED
