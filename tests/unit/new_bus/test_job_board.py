from __future__ import annotations

import threading
from datetime import datetime

from magi.bus import BaseJobResult, Bus, JobStatus, Slot
from tests.unit.new_bus.testing import WORKER, PingBus, PingJob, PingJobBoard, attach_board


def test_publish_claim_complete(ping_board) -> None:
    published = PingJob(n=1, publisher="worker-a")
    published.id = ping_board.publish(published)
    assert published.id
    assert ping_board.get_result(published.id) is None
    assert ping_board.check_job_status(published.id) is JobStatus.PENDING

    claimed = ping_board.claim()
    assert claimed is not None
    assert claimed.id == published.id
    assert ping_board.get_result(claimed.id) is None
    assert ping_board.check_job_status(claimed.id) is JobStatus.CLAIMED
    assert claimed.n == 1
    assert isinstance(claimed.created_at, datetime)

    ping_board.submit_result(BaseJobResult(id=claimed.id))
    outcome = ping_board.get_result(claimed.id)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.id == claimed.id
    again = ping_board.get_result(claimed.id)
    assert again is not None
    assert again.status is JobStatus.COMPLETED
    assert not hasattr(claimed, "result")
    assert not hasattr(claimed, "status")
    assert not hasattr(claimed, "error")


def test_job_and_result_share_one_flat_record(ping_board) -> None:
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    outcome = ping_board.get_result(claimed.id)
    assert outcome is not None
    assert outcome.id == claimed.id
    assert outcome.status is JobStatus.COMPLETED
    assert not hasattr(claimed, "result")
    assert not hasattr(claimed, "status")


def test_claim_then_fail(ping_board) -> None:
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="nope"))
    outcome = ping_board.get_result(claimed.id)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "nope"


def test_claim_empty_board(ping_board) -> None:
    assert ping_board.claim() is None


def test_illegal_complete_from_pending(ping_board) -> None:
    job = PingJob()
    job.id = ping_board.publish(job)
    assert not ping_board.submit_result(BaseJobResult(id=job.id))


def test_complete_twice_is_illegal(ping_board) -> None:
    ping_board.publish(PingJob())
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    assert not ping_board.submit_result(BaseJobResult(id=claimed.id))


def test_list_filters_status(ping_board) -> None:
    first_id = ping_board.publish(PingJob(n=1))
    ping_board.publish(PingJob(n=2))
    claimed = ping_board.claim()
    assert claimed is not None
    ping_board.submit_result(BaseJobResult(id=claimed.id))
    pending = ping_board.list(status=JobStatus.PENDING)
    completed = ping_board.list(status=JobStatus.COMPLETED)
    assert [job.n for job in pending] == [2]
    assert [job.id for job in completed] == [first_id]


def test_claim_is_exclusive(tmp_path) -> None:
    with PingBus(tmp_path) as bus:
        ping_board = attach_board(
            bus,
            PingJobBoard,
            worker_id=WORKER,
            slots=("publish", "claim", "submit_result"),
        )
        for index in range(20):
            ping_board.publish(PingJob(n=index))

        claimed_ids: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                job = ping_board.claim()
                if job is None:
                    return
                with lock:
                    claimed_ids.append(job.id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(claimed_ids) == 20
        assert len(set(claimed_ids)) == 20


def test_unmounted_job_is_invalid(tmp_path) -> None:
    with Bus(tmp_path) as bus:
        assert bus.for_worker(WORKER, (Slot(PingJob, "publish"),)) is None


def test_worker_client_hides_backend_failure(tmp_path, monkeypatch) -> None:
    with PingBus(tmp_path) as bus:
        client = attach_board(bus, PingJobBoard, worker_id=WORKER, slots=("publish",))
        board = bus._job_board(PingJob)
        assert board is not None

        def fail(*_args, **_kwargs):
            raise OSError("database unavailable")

        monkeypatch.setattr(board, "publish", fail)
        assert client.publish(PingJob()) == 0
