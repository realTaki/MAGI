"""Unit tests for ChannelWorker.health() and /health/channels endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from magi.channels.api.health import health_channels
from magi.channels.worker_base import ChannelWorker


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
    w._last_poll_at = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    w._last_success_at = datetime(2026, 8, 8, 12, 5, 0, tzinfo=UTC)
    w._last_error = None
    w._children = set()
    w._queue_depth = 3
    w.worker_name = "test_ch"
    w.concurrency = 2
    w.worker_kind = "channel"
    w.bus = MagicMock()

    h = w.health()
    assert h["name"] == "test_ch"
    assert h["running"] is False
    assert h["last_poll_at"] is not None
    assert h["last_success_at"] is not None
    assert h["last_error"] is None
    assert h["queue_depth"] == 3
    assert h["concurrency"] == 2
    assert h["kind"] == "channel"


def test_health_returns_none_when_not_polled():
    """When worker hasn't polled yet, timestamps are None."""
    w = _FakeHealthWorker.__new__(_FakeHealthWorker)
    w.channel_name = "fresh"
    w._task = None
    w._last_poll_at = None
    w._last_success_at = None
    w._last_error = None
    w._children = set()
    w._queue_depth = 0
    w.worker_name = "fresh"
    w.concurrency = 1
    w.worker_kind = "channel"
    w.bus = MagicMock()

    h = w.health()
    assert h["last_poll_at"] is None
    assert h["last_success_at"] is None
    assert h["queue_depth"] == 0
    assert h["concurrency"] == 1


@pytest.mark.asyncio
async def test_health_endpoint_returns_empty_when_no_workers():
    """/health/channels is empty when this ASGI app has no registry."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workers=None)))
    result = await health_channels(request)
    assert result == {"channels": []}
