from magi.bus import BaseJobResult, JobStatus, Slot
from tests.unit.new_bus.testing import PingBus, PingJob, PingJobBoard, attach_board


def test_and_dock_waits_for_live_members_and_rejects_on_any_failure(tmp_path) -> None:
    with PingBus(tmp_path) as bus:
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

        slot = Slot(PingJob, "submit_result")
        assert bus.install_and_dock(slot)
        first = bus.for_worker("first", (slot,))
        second = bus.for_worker("second", (slot,))
        assert first is not None
        assert second is not None

        assert first.board(PingJob).submit_result(
            BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="rejected")
        )
        assert direct.check_job_status(job.id) is JobStatus.CLAIMED
        assert second.board(PingJob).submit_result(BaseJobResult(id=claimed.id))

        result = direct.get_result(job.id)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error == "rejected"
