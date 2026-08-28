from magi.new_bus import BaseJobResult, JobStatus, Slot
from tests.unit.new_bus.testing import InMemoryBackend, PingBus, PingJob

_SLOTS = (
    Slot(PingJob, "publish"),
    Slot(PingJob, "claim"),
    Slot(PingJob, "submit_result"),
)


def test_or_dock_routes_typed_worker_board_calls() -> None:
    with PingBus(InMemoryBackend()) as bus:
        for slot in _SLOTS:
            assert bus.install_or_dock(slot)
        first = bus.for_worker("worker-a", _SLOTS)
        second = bus.for_worker("worker-b", _SLOTS)
        assert first is not None
        assert second is not None

        job = PingJob(n=7)
        job.id = first.board(PingJob).publish(job)
        claimed = first.board(PingJob).claim()
        assert claimed is not None
        assert claimed.id == job.id

        assert second.board(PingJob).submit_result(
            BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="worker-b decided")
        )
        assert not first.board(PingJob).submit_result(BaseJobResult(id=claimed.id))
        result = first.board(PingJob).get_result(job.id)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error == "worker-b decided"


def test_worker_heartbeat_renews_every_dock_membership() -> None:
    with PingBus(InMemoryBackend()) as bus:
        for slot in _SLOTS:
            assert bus.install_or_dock(slot)
        worker = bus.for_worker("worker", _SLOTS)
        assert worker is not None
        assert worker.heartbeat()
        assert worker.is_alive()


def test_worker_without_claim_slot_cannot_claim_a_routed_board() -> None:
    with PingBus(InMemoryBackend()) as bus:
        assert bus.install_or_dock(Slot(PingJob, "claim"))
        publisher = bus.for_worker("publisher", (Slot(PingJob, "publish"),))
        assert publisher is not None
        assert publisher.board(PingJob).claim() is None
