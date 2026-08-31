from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from bus import BaseJobResult, Bus, JobStatus, go
from tests.unit.new_bus.testing import PingBus, PingJob, PingJobBoard, attach_board, wait_publish


def _submit(board, result):
    return go(board.submit_result(result)).result()


def _claim(board):
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        job = board.claim()
        if job is not None:
            return job
        time.sleep(0.01)
    return None


def test_publish_claim_complete(ping_board) -> None:
    published = PingJob(n=1, publisher="worker-a")
    published.id = wait_publish(ping_board, published)
    assert published.id
    assert ping_board.check_job_status(published.id) in {
        JobStatus.PREPARING,
        JobStatus.PENDING,
    }

    claimed = _claim(ping_board)
    assert claimed is not None
    assert claimed.id == published.id
    assert ping_board.check_job_status(claimed.id) is JobStatus.CLAIMED
    assert claimed.n == 1
    assert isinstance(claimed.created_at, datetime)

    _submit(ping_board, BaseJobResult(id=claimed.id))
    outcome = go(ping_board.get_result(claimed.id)).result()
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.id == claimed.id
    again = go(ping_board.get_result(claimed.id)).result()
    assert again is not None
    assert again.status is JobStatus.COMPLETED
    assert not hasattr(claimed, "result")
    assert not hasattr(claimed, "status")
    assert not hasattr(claimed, "error")


def test_job_and_result_share_one_flat_record(ping_board) -> None:
    wait_publish(ping_board, PingJob())
    claimed = _claim(ping_board)
    assert claimed is not None
    _submit(ping_board, BaseJobResult(id=claimed.id))
    outcome = go(ping_board.get_result(claimed.id)).result()
    assert outcome is not None
    assert outcome.id == claimed.id
    assert outcome.status is JobStatus.COMPLETED
    assert not hasattr(claimed, "result")
    assert not hasattr(claimed, "status")


def test_claim_then_fail(ping_board) -> None:
    wait_publish(ping_board, PingJob())
    claimed = _claim(ping_board)
    assert claimed is not None
    _submit(ping_board, BaseJobResult(id=claimed.id, status=JobStatus.FAILED, error="nope"))
    outcome = go(ping_board.get_result(claimed.id)).result()
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "nope"


def test_claim_empty_board(ping_board) -> None:
    assert ping_board.claim() is None


def test_illegal_complete_from_pending(ping_board) -> None:
    job = PingJob()
    job.id = wait_publish(ping_board, job)
    assert not _submit(ping_board, BaseJobResult(id=job.id))


def test_complete_twice_is_illegal(ping_board) -> None:
    wait_publish(ping_board, PingJob())
    claimed = _claim(ping_board)
    assert claimed is not None
    _submit(ping_board, BaseJobResult(id=claimed.id))
    assert not _submit(ping_board, BaseJobResult(id=claimed.id))


def test_list_filters_status(ping_board) -> None:
    first_id = wait_publish(ping_board, PingJob(n=1))
    wait_publish(ping_board, PingJob(n=2))
    claimed = ping_board.claim()
    assert claimed is not None
    _submit(ping_board, BaseJobResult(id=claimed.id))
    pending = ping_board.list(status=JobStatus.PENDING)
    completed = ping_board.list(status=JobStatus.COMPLETED)
    assert [job.n for job in pending] == [2]
    assert [job.id for job in completed] == [first_id]


def test_claim_is_exclusive(tmp_path) -> None:
    with PingBus(tmp_path) as bus:
        ping_board = attach_board(bus, PingJobBoard)
        for index in range(20):
            wait_publish(ping_board, PingJob(n=index))

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
        with pytest.raises(KeyError, match="PingJob"):
            bus.board(PingJob)
