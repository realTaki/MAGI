from __future__ import annotations

from magi.new_bus import BaseJobResult, JobBoardClient, JobStatus, Slot, WorkerBus, job_board
from tests.unit.new_bus.testing import InMemoryBackend, PingBus, PingJob, PingJobBoard, attach_board


class ResultWorkerBus(WorkerBus):
    pingJobBoard: JobBoardClient[PingJob, BaseJobResult] = job_board(
        PingJobBoard,
        slots=("submit_result",),
    )


def test_and_dock_waits_for_live_members_and_rejects_on_any_failure() -> None:
    with PingBus(InMemoryBackend()) as bus:
        direct = attach_board(
            bus,
            PingJobBoard,
            worker_id="direct",
            slots=("publish", "claim"),
        )
        job = PingJob()
        job.id = direct.publish(job)
        claimed = direct.claim()
        assert claimed is not None

        assert bus.install_and_dock(Slot(PingJob, "submit_result"))
        first = bus.for_worker("first", ResultWorkerBus)
        second = bus.for_worker("second", ResultWorkerBus)
        assert first.attach()
        assert second.attach()

        assert first.pingJobBoard.submit_result(
            BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="rejected")
        )
        assert direct.check_job_status(job.id) is JobStatus.CLAIMED
        assert second.pingJobBoard.submit_result(BaseJobResult(id=claimed.id))

        result = direct.get_result(job.id)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error == "rejected"
