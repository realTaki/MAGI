"""Unit tests for runTaskJobBoard — publish/claim/submit_result lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from old_bus.bases.db import EngineFactory
from old_bus.bases.job import JobStatus
from old_bus.firmwares.jobs.runTaskJob import (
    RunTaskJob,
    RunTaskResult,
    runTaskJobBoard,
)


@pytest.fixture
def board():
    """Fresh in-memory SQLite with runTaskJobBoard per test."""
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return runTaskJobBoard(f)


def test_publish_returns_job_id(board):
    """publish returns a database-generated integer job_id."""
    job = RunTaskJob(
        task_id="task_abc",
        manual=True,
    )
    jid = board.publish(job)
    assert jid == 1


def test_claim_returns_published_job(board):
    """claim returns the job we just published with manual flag preserved."""
    board.publish(RunTaskJob(task_id="task_x", manual=False))
    claim = board.claim(worker_id="worker-a")
    assert claim is not None
    assert claim.task_id == "task_x"
    assert claim.manual is False


def test_claim_returns_none_when_empty(board):
    """claim returns None when no pending jobs."""
    assert board.claim(worker_id="worker-a") is None


def test_submit_result_success(board):
    """submit_result marks job as completed and get_result returns it."""
    jid = board.publish(RunTaskJob(task_id="task_s", manual=True))
    claim = board.claim(worker_id="worker-a")
    assert claim is not None

    board.submit_result(
        job_id=jid,
        worker_id="worker-a",
        result=RunTaskResult(job_id=jid, status=JobStatus.COMPLETED),
    )
    result = board.get_result(job_id=jid)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.error is None


def test_submit_result_failure(board):
    """submit_result with success=False returns error info."""
    jid = board.publish(RunTaskJob(task_id="task_f", manual=True))
    claim = board.claim(worker_id="worker-a")
    assert claim is not None

    board.submit_result(
        job_id=jid,
        worker_id="worker-a",
        result=RunTaskResult(job_id=jid, status=JobStatus.FAILED, error="task not found"),
    )
    result = board.get_result(job_id=jid)
    assert result is not None
    assert result.status == JobStatus.FAILED
    assert result.error == "task not found"


def test_submit_result_key_owns_the_primary_key(board):
    """The result payload cannot overwrite the auto-incrementing job key."""
    jid = board.publish(RunTaskJob(task_id="task_key", manual=True))
    assert board.claim(worker_id="worker-a") is not None

    board.submit_result(
        job_id=jid,
        worker_id="worker-a",
        result=RunTaskResult(status=JobStatus.COMPLETED),
    )

    result = board.get_result(job_id=jid)
    assert result is not None
    assert result.job_id == jid


def test_lease_expiry_reclaims_abandoned_job(board, monkeypatch):
    """Abandoned job (lease expired) is re-claimed by next claim()."""
    # Short lease for fast test
    board._lease_seconds = 1
    jid = board.publish(RunTaskJob(task_id="task_a", manual=False))
    first = board.claim(worker_id="worker-a")
    assert first is not None

    # Simulate lease expiry by manipulating leased_until
    from datetime import timedelta

    from old_bus.bases.db.base import utcnow_naive
    from old_bus.firmwares.jobs.runTaskJob import _RunTaskJobRow

    with board._session() as s:
        row = s.scalar(select(_RunTaskJobRow).where(_RunTaskJobRow.job_id == jid))
        if row:
            row.leased_until = utcnow_naive() - timedelta(seconds=10)
            s.commit()

    second = board.claim(worker_id="worker-b")
    assert second is not None
    assert second.task_id == "task_a"
    with board._session() as s:
        row = s.scalar(select(_RunTaskJobRow).where(_RunTaskJobRow.job_id == second.job_id))
        assert row is not None
        assert row.leased_by == "worker-b"


def test_expired_lease_remains_claimable_without_bus_failure(board):
    """Lease recovery never lets BUS invent an exhausted/failed result."""
    jid = board.publish(RunTaskJob(task_id="task_ex", manual=False))
    board._lease_seconds = 1

    from datetime import timedelta

    from old_bus.bases.db.base import utcnow_naive
    from old_bus.firmwares.jobs.runTaskJob import _RunTaskJobRow

    for worker_id in ("worker-a", "worker-b", "worker-c", "worker-d"):
        claim = board.claim(worker_id=worker_id)
        assert claim is not None
        # expire lease
        with board._session() as s:
            row = s.scalar(select(_RunTaskJobRow).where(_RunTaskJobRow.job_id == jid))
            if row:
                row.leased_until = utcnow_naive() - timedelta(seconds=10)
                s.commit()

    assert board.get_result(job_id=jid) is None


def test_submit_result_ignores_a_worker_that_does_not_hold_the_lease(board):
    jid = board.publish(RunTaskJob(task_id="task_owner", manual=True))
    assert board.claim(worker_id="worker-a") is not None

    board.submit_result(
        job_id=jid,
        worker_id="worker-b",
        result=RunTaskResult(job_id=jid, status=JobStatus.COMPLETED),
    )

    assert board.get_result(job_id=jid) is None
    board.submit_result(
        job_id=jid,
        worker_id="worker-a",
        result=RunTaskResult(job_id=jid, status=JobStatus.COMPLETED),
    )
    assert board.get_result(job_id=jid) is not None
