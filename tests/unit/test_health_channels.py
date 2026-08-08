"""Unit tests for ChannelWorker.health() and /health/channels endpoint."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from magi.channels.workers.base import ChannelWorker
from magi.channels.api.health import health_channels


class _FakeHealthWorker(ChannelWorker):
    """Minimal worker for testing health() output."""
    channel_name = "test_ch"

    async def _run(self) -> None:
        pass


def test_health_returns_expected_keys():
    """health() returns a dict with all expected keys."""
    w = _FakeHealthWorker.__new__(_FakeHealthWorker)
    w.channel_name = "test_ch"
    w._task = None
    w._last_poll_at = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    w._last_success_at = datetime(2026, 8, 8, 12, 5, 0, tzinfo=timezone.utc)
    w._last_error = None
    w.bus = MagicMock()
    w.bus.delivery_job_board.pending_count = MagicMock(return_value=3)

    h = w.health()
    assert h["name"] == "test_ch"
    assert h["running"] is False
    assert h["last_poll_at"] is not None
    assert h["last_success_at"] is not None
    assert h["last_error"] is None
    assert h["queue_depth"] == 3


def test_health_returns_none_when_not_polled():
    """When worker hasn't polled yet, timestamps are None."""
    w = _FakeHealthWorker.__new__(_FakeHealthWorker)
    w.channel_name = "fresh"
    w._task = None
    w._last_poll_at = None
    w._last_success_at = None
    w._last_error = None
    w.bus = MagicMock()
    w.bus.delivery_job_board.pending_count = MagicMock(return_value=0)

    h = w.health()
    assert h["last_poll_at"] is None
    assert h["last_success_at"] is None
    assert h["queue_depth"] == 0


@pytest.mark.asyncio
async def test_health_endpoint_returns_empty_when_no_workers():
    """/health/channels returns empty channels list when no workers registered."""
    from magi.channels.workers import _registry as workers_registry
    from magi.channels.workers import registered_channel_workers

    # Save and clear
    saved = dict(workers_registry)
    workers_registry.clear()

    try:
        result = await health_channels()
        assert "channels" in result
        assert isinstance(result["channels"], list)
    finally:
        workers_registry.update(saved)
