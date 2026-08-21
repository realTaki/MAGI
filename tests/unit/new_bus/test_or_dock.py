from __future__ import annotations

from magi.new_bus import BaseJobResult, Bus, JobBoardClient, JobStatus, Slot, WorkerBus, job_board
from tests.unit.new_bus.testing import InMemoryBackend, PingJob, PingJobBoard


class PingWorkerBus(WorkerBus):
    pingJobBoard: JobBoardClient[PingJob, BaseJobResult] = job_board(
        PingJobBoard,
        slots=("publish", "claim", "submit_result"),
    )


def test_or_dock_routes_typed_worker_board_calls() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        for name in ("publish", "claim", "submit_result"):
            assert bus.install_or_dock(Slot(PingJob, name))
        first = bus.for_worker("worker-a", PingWorkerBus)
        second = bus.for_worker("worker-b", PingWorkerBus)
        assert first.attach()
        assert second.attach()

        job = PingJob(n=7)
        job.id = first.pingJobBoard.publish(job)
        claimed = first.pingJobBoard.claim()
        assert claimed is not None
        assert claimed.id == job.id

        assert second.pingJobBoard.submit_result(
            BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="worker-b decided")
        )
        assert not first.pingJobBoard.submit_result(BaseJobResult(id=claimed.id))
        result = bus.get_result(job)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error == "worker-b decided"


def test_worker_heartbeat_renews_every_dock_membership() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        for name in ("publish", "claim", "submit_result"):
            assert bus.install_or_dock(Slot(PingJob, name))
        worker = bus.for_worker("worker", PingWorkerBus)
        assert worker.attach()
        assert worker.heartbeat()
        assert worker.is_alive()


def test_unattached_worker_cannot_use_a_routed_board() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        assert bus.install_or_dock(Slot(PingJob, "claim"))
        worker = bus.for_worker("outsider", PingWorkerBus)
        assert worker.pingJobBoard.claim() is None
