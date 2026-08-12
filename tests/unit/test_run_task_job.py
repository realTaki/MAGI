"""Unit tests for runTaskJobBoard — publish/claim/submit_result lifecycle."""

from __future__ import annotations

import pytest

from magi.bus.db import EngineFactory
from magi.bus.guild.base import JobStatus
from magi.bus.guild.runTaskJob import (
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
    """publish returns a non-empty hex job_id."""
    job = RunTaskJob(
        task_id="task_abc",
        manual=True,
    )
    jid = board.publish(job)
    assert isinstance(jid, str)
    assert len(jid) > 0
    assert jid != ""


def test_claim_returns_published_job(board):
    """claim returns the job we just published with manual flag preserved."""
    board.publish(RunTaskJob(task_id="task_x", manual=False))
    claim = board.claim()
    assert claim is not None
    assert claim.task_id == "task_x"
    assert claim.manual is False


def test_claim_returns_none_when_empty(board):
    """claim returns None when no pending jobs."""
    assert board.claim() is None


def test_submit_result_success(board):
    """submit_result marks job as completed and get_result returns it."""
    jid = board.publish(RunTaskJob(task_id="task_s", manual=True))
    claim = board.claim()
    assert claim is not None

    board.submit_result(
        key=jid,
        result=RunTaskResult(job_id=jid, status=JobStatus.COMPLETED),
    )
    result = board.get_result(key=jid)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.error is None


def test_submit_result_failure(board):
    """submit_result with success=False returns error info."""
    jid = board.publish(RunTaskJob(task_id="task_f", manual=True))
    claim = board.claim()
    assert claim is not None

    board.submit_result(
        key=jid,
        result=RunTaskResult(job_id=jid, status=JobStatus.FAILED, error="task not found"),
    )
    result = board.get_result(key=jid)
    assert result is not None
    assert result.status == JobStatus.FAILED
    assert result.error == "task not found"


def test_lease_expiry_reclaims_abandoned_job(board, monkeypatch):
    """Abandoned job (lease expired) is re-claimed by next claim()."""
    # Short lease for fast test
    board._lease_seconds = 1
    jid = board.publish(RunTaskJob(task_id="task_a", manual=False))
    first = board.claim()
    assert first is not None

    # Simulate lease expiry by manipulating leased_until
    from datetime import timedelta

    from sqlalchemy import select

    from magi.bus.db.base import utcnow_naive
    from magi.bus.guild.runTaskJob import _RunTaskJobRow

    with board._session() as s:
        row = s.scalar(select(_RunTaskJobRow).where(_RunTaskJobRow.job_id == jid))
        if row:
            row.leased_until = utcnow_naive() - timedelta(seconds=10)
            s.commit()

    second = board.claim()
    assert second is not None
    assert second.task_id == "task_a"
    assert second.attempts > first.attempts  # attempts incremented on re-claim


def test_max_attempts_exhausted(board):
    """After MAX_ATTEMPTS (3), claim marks job as failed and skips it."""
    from magi.bus.guild.base import MAX_ATTEMPTS

    jid = board.publish(RunTaskJob(task_id="task_ex", manual=False))
    board._lease_seconds = 1

    from datetime import timedelta

    from sqlalchemy import select

    from magi.bus.db.base import utcnow_naive
    from magi.bus.guild.runTaskJob import _RunTaskJobRow

    for _ in range(MAX_ATTEMPTS + 1):
        claim = board.claim()
        if claim is None:
            break
        # expire lease
        with board._session() as s:
            row = s.scalar(select(_RunTaskJobRow).where(_RunTaskJobRow.job_id == jid))
            if row:
                row.leased_until = utcnow_naive() - timedelta(seconds=10)
                s.commit()

    # After MAX_ATTEMPTS, job should be exhausted and claim returns None
    exhausted_claim = board.claim()
    assert exhausted_claim is None  # exhausted, no longer claimable

    result = board.get_result(key=jid)
    assert result is not None
    assert result.status == JobStatus.FAILED
