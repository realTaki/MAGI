"""Unit tests for :class:`mcpServerChangedJobBoard`.

Exercises the publish → claim → submit_result round-trip on a
fresh in-memory SQLite, plus the validation contract the
:class:`~magi.mcp.worker.McpWorker` relies on (unknown kinds,
empty server name, duplicate publish semantics).
"""

from __future__ import annotations

import pytest

from magi.new_bus.db import EngineFactory
from magi.new_bus.guild import (
    McpServerChangedJob,
    McpServerChangedResult,
    mcpServerChangedJobBoard,
)


@pytest.fixture
def factory():
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


@pytest.fixture
def board(factory):
    return mcpServerChangedJobBoard(factory)


# -- input validation ---------------------------------------------------


def test_job_validation_rejects_unknown_kind():
    with pytest.raises(ValueError, match="invalid kind"):
        McpServerChangedJob(kind="rotated", server_name="gmail")


def test_job_validation_rejects_blank_server_name():
    with pytest.raises(ValueError, match="server_name is required"):
        McpServerChangedJob(kind="added", server_name="")


# -- round-trip ---------------------------------------------------------


def test_publish_assigns_job_id_and_persists(board, factory):
    job_id = board.publish(
        McpServerChangedJob(kind="added", server_name="gmail")
    )
    assert isinstance(job_id, str) and job_id
    # The row landed in the table.
    from sqlalchemy import select

    from magi.new_bus.guild.mcpServerChangedJob import _McpServerChangedRow

    with factory.session() as s:
        row = s.scalar(
            select(_McpServerChangedRow).where(
                _McpServerChangedRow.job_id == job_id
            )
        )
    assert row is not None
    assert row.status == "pending"
    assert row.kind == "added"
    assert row.server_name == "gmail"


def test_publish_respects_caller_supplied_job_id(board):
    custom = "job-abc-123"
    job_id = board.publish(
        McpServerChangedJob(
            kind="updated", server_name="gmail", job_id=custom
        )
    )
    assert job_id == custom


def test_claim_returns_none_when_empty(board):
    assert board.claim() is None


def test_claim_then_submit_result_round_trip(board):
    job_id = board.publish(
        McpServerChangedJob(kind="toggled", server_name="gmail")
    )
    claimed = board.claim()
    assert claimed is not None
    assert claimed.kind == "toggled"
    assert claimed.server_name == "gmail"
    assert claimed.job_id == job_id

    board.submit_result(
        key=job_id,
        result=McpServerChangedResult(
            job_id=job_id, success=True, error=None
        ),
    )
    result = board.get_result(key=job_id)
    assert result is not None
    assert result.success is True
    assert result.error is None


def test_submit_result_records_error(board):
    job_id = board.publish(
        McpServerChangedJob(kind="deleted", server_name="gmail")
    )
    board.claim()
    board.submit_result(
        key=job_id,
        result=McpServerChangedResult(
            job_id=job_id, success=False, error="boom"
        ),
    )
    result = board.get_result(key=job_id)
    assert result is not None
    assert result.success is False
    assert result.error == "boom"


def test_valid_kinds_constant_matches_documented_set():
    from magi.new_bus.guild.mcpServerChangedJob import VALID_KINDS

    assert VALID_KINDS == frozenset({"added", "updated", "deleted", "toggled"})
