"""Unit tests for :class:`changeMCPServerJobBoard`.

Exercises the publish → claim → submit_result round-trip on a
fresh in-memory SQLite, plus the validation contract the
:class:`~mcp.worker.McpWorker` relies on (unknown kinds,
empty server name, payload requirements by kind, duplicate
publish semantics).
"""

from __future__ import annotations

import pytest

from old_bus.bases.db import EngineFactory
from old_bus.firmwares.schema import LOCAL_SCOPE, synchronise_schema
from old_bus.firmwares.jobs import (
    ChangeMCPServerJob,
    ChangeMCPServerResult,
    MCPKind,
    changeMCPServerJobBoard,
)
from old_bus.bases.job import JobStatus
from old_bus.firmwares.books.local.mcpServerBook import McpServer


@pytest.fixture
def factory():
    f = EngineFactory("sqlite:///:memory:")
    synchronise_schema(f, scope=LOCAL_SCOPE)
    return f


@pytest.fixture
def board(factory):
    return changeMCPServerJobBoard(factory)


def _gmail_dto() -> McpServer:
    """Minimal :class:`McpServer` DTO for ``added`` / ``updated`` jobs."""
    return McpServer(
        name="gmail",
        connection_type="stdio",
        command="mcp-gmail",
        env={},
        headers={},
        enabled=True,
    )


# -- input validation ---------------------------------------------------


def test_job_validation_rejects_unknown_kind():
    with pytest.raises(ValueError, match="invalid kind"):
        ChangeMCPServerJob(kind="rotated", server_name="gmail")


def test_job_validation_rejects_blank_server_name():
    with pytest.raises(ValueError, match="server_name is required"):
        ChangeMCPServerJob(kind=MCPKind.DELETED, server_name="")


def test_job_validation_requires_server_payload_for_added():
    with pytest.raises(ValueError, match="requires a McpServer payload"):
        ChangeMCPServerJob(kind=MCPKind.ADDED, server_name="gmail")


def test_job_validation_requires_server_payload_for_updated():
    with pytest.raises(ValueError, match="requires a McpServer payload"):
        ChangeMCPServerJob(kind=MCPKind.UPDATED, server_name="gmail")


def test_job_validation_requires_new_enabled_for_toggled():
    with pytest.raises(ValueError, match="requires new_enabled"):
        ChangeMCPServerJob(kind=MCPKind.TOGGLED, server_name="gmail")


# -- round-trip ---------------------------------------------------------


def test_publish_assigns_job_id_and_persists(board, factory):
    job_id = board.publish(
        ChangeMCPServerJob(kind=MCPKind.ADDED, server_name="gmail", server=_gmail_dto())
    )
    assert isinstance(job_id, int) and job_id > 0
    # The row landed in the table.
    from sqlalchemy import select

    from old_bus.firmwares.jobs.changeMCPServerJob import _ChangeMCPServerRow

    with factory.session() as s:
        row = s.scalar(select(_ChangeMCPServerRow).where(_ChangeMCPServerRow.job_id == job_id))
    assert row is not None
    assert row.status == JobStatus.PENDING
    assert row.kind == "added"
    assert row.server_name == "gmail"
    # Payload survives the publish round-trip.
    assert row.server_payload is not None
    assert row.server_payload["name"] == "gmail"
    assert row.server_payload["command"] == "mcp-gmail"


def test_publish_assigns_distinct_auto_incrementing_job_ids(board):
    """Each publish receives a distinct database-generated primary key."""
    first = board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.UPDATED,
            server_name="gmail",
            server=_gmail_dto(),
        )
    )
    second = board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.UPDATED,
            server_name="gmail",
            server=_gmail_dto(),
        )
    )
    assert (first, second) == (1, 2)


def test_claim_returns_none_when_empty(board):
    assert board.claim(worker_id="mcp-worker") is None


def test_claim_then_submit_result_round_trip(board):
    job_id = board.publish(
        ChangeMCPServerJob(
            kind=MCPKind.TOGGLED,
            server_name="gmail",
            new_enabled=False,
        )
    )
    claimed = board.claim(worker_id="mcp-worker")
    assert claimed is not None
    assert claimed.kind == "toggled"
    assert claimed.server_name == "gmail"
    assert claimed.new_enabled is False
    assert claimed.job_id == job_id

    board.submit_result(
        job_id=job_id,
        worker_id="mcp-worker",
        result=ChangeMCPServerResult(job_id=job_id, status=JobStatus.COMPLETED, error=None),
    )
    result = board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.error is None


def test_claim_round_trips_added_payload_into_dto(board):
    """``claim()`` materialises ``server_payload`` back into a
    :class:`McpServer` so the Worker sees a fully populated DTO."""
    job_id = board.publish(
        ChangeMCPServerJob(kind=MCPKind.ADDED, server_name="gmail", server=_gmail_dto())
    )
    claimed = board.claim(worker_id="mcp-worker")
    assert claimed is not None
    assert claimed.server is not None
    assert claimed.server.name == "gmail"
    assert claimed.server.command == "mcp-gmail"
    assert claimed.server.connection_type == "stdio"
    assert claimed.server.enabled is True
    assert claimed.job_id == job_id


def test_submit_result_records_error(board):
    job_id = board.publish(ChangeMCPServerJob(kind=MCPKind.DELETED, server_name="gmail"))
    board.claim(worker_id="mcp-worker")
    board.submit_result(
        job_id=job_id,
        worker_id="mcp-worker",
        result=ChangeMCPServerResult(job_id=job_id, status=JobStatus.FAILED, error="boom"),
    )
    result = board.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.FAILED
    assert result.error == "boom"


def test_mcp_kind_enum_matches_documented_set():
    """Locks the four :class:`MCPKind` members; adding a fifth
    without a docstring / payload-shape update will fail here."""
    from old_bus.firmwares.jobs.changeMCPServerJob import MCPKind

    assert {k.value for k in MCPKind} == {"added", "updated", "deleted", "toggled"}
    # StrEnum contract: members compare equal to their string value
    # so ORM rows, JSON serialisation, and `==` against literals
    # keep working unchanged.
    assert MCPKind.ADDED == "added"
    assert MCPKind.UPDATED == "updated"
    assert MCPKind.DELETED == "deleted"
    assert MCPKind.TOGGLED == "toggled"
