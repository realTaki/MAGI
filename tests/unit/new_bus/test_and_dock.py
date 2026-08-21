from __future__ import annotations

from magi.new_bus import BaseJobResult, Bus, JobBoardClient, JobStatus, Slot, WorkerBus, job_board
from tests.unit.new_bus.support import InMemoryBackend, PingJob, PingJobBoard


class ResultWorkerBus(WorkerBus):
    pingJobBoard: JobBoardClient[PingJob, BaseJobResult] = job_board(
        PingJobBoard,
        slots=("submit_result",),
    )


def test_and_dock_waits_for_live_members_and_rejects_on_any_failure() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        assert bus.attach("direct", (Slot(PingJob, "publish"), Slot(PingJob, "claim")))
        job = PingJob()
        job.id = bus.publish(job, worker_id="direct")
        claimed = bus.claim(PingJob, worker_id="direct")
        assert claimed is not None

        assert bus.install_and_dock(Slot(PingJob, "submit_result"))
        first = bus.for_worker("first", ResultWorkerBus)
        second = bus.for_worker("second", ResultWorkerBus)
        assert first.attach()
        assert second.attach()

        assert first.pingJobBoard.submit_result(
            BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="rejected")
        )
        assert bus.check_job_status(job) is JobStatus.CLAIMED
        assert second.pingJobBoard.submit_result(BaseJobResult(id=claimed.id))

        result = bus.get_result(job)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error == "rejected"
