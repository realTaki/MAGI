from __future__ import annotations

from magi.new_bus import BaseJobResult, Bus, JobStatus, OrDock
from magi.new_bus.testing import InMemoryBackend, PingJob, PingJobBoard


def test_or_dock_shares_one_slot_between_workers() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        publisher = OrDock("publish", name="ping")
        claimer = OrDock("claim", name="ping")
        submitter = OrDock("submit_result", name="ping")
        for dock in (publisher, claimer, submitter):
            assert dock.attach(bus, "worker-a", PingJob)
            assert dock.attach(bus, "worker-b", PingJob)

        job = PingJob(n=7)
        job.id = publisher.call("worker-a", job)
        claimed = claimer.call("worker-a")
        assert claimed is not None
        assert claimed.id == job.id

        assert submitter.call(
            "worker-b",
            BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="worker-b decided"),
        )
        assert not submitter.call(
            "worker-a",
            BaseJobResult(id=claimed.id),
        )
        result = bus.get_result(job)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error == "worker-b decided"


def test_or_dock_rejects_unattached_workers() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        dock = OrDock("claim")
        assert dock.attach(bus, "member", PingJob)

        assert dock.call("outsider") is None


def test_or_dock_cannot_take_a_slot_held_by_another_worker() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        assert bus.attach("other", PingJob, ("claim",))

        dock = OrDock("claim", name="contended")
        assert not dock.attach(bus, "member", PingJob)
