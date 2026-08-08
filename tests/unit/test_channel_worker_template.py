"""Unit tests for ChannelWorker base class _claim_delivery_loop template.

Tests the base class template method with a fake deliver_fn, verifying:
- claim → deliver → submit_result(success=True) flow
- deliver_fn failure → submit_result(success=False) flow
- backpressure branch when pending_count exceeds max_depth
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from magi.channels.workers.base import ChannelWorker
from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult


class _FakeChannelWorker(ChannelWorker):
    """Minimal concrete ChannelWorker for testing the base class template."""
    channel_name = "fake"

    async def _run(self) -> None:
        await self._claim_delivery_loop(self._deliver, "fake")

    async def _deliver(self, job: DeliveryJob) -> None:
        pass  # overridden in tests


@pytest.mark.asyncio
async def test_successful_delivery_calls_submit_result_with_success():
    """A successful deliver_fn should submit_result(success=True)."""
    delivered: list[DeliveryJob] = []

    w = _FakeChannelWorker.__new__(_FakeChannelWorker)
    w.channel_name = "fake"
    w.poll_seconds = 0.01
    w._stopping = False
    w._last_poll_at = None
    w._last_success_at = None
    w._last_error = None

    # Mock bus
    fake_job = DeliveryJob(channel="fake", payload={"text": "hi"}, job_id="j1")
    w.bus = MagicMock()
    w.bus.delivery_job_board.claim = MagicMock(
        side_effect=[fake_job, None]
    )
    w.bus.delivery_job_board.submit_result = MagicMock()
    w.bus.delivery_job_board.pending_count = MagicMock(return_value=0)
    w.bus.settings_book.get = MagicMock(return_value=None)  # default depth

    async def deliver(job):
        delivered.append(job)

    w._deliver = deliver

    # Run one iteration then stop
    task = asyncio.create_task(w._claim_delivery_loop(w._deliver, "fake"))
    await asyncio.sleep(0.05)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    assert len(delivered) == 1
    w.bus.delivery_job_board.submit_result.assert_called()
    call_args = w.bus.delivery_job_board.submit_result.call_args
    result = call_args.kwargs["result"]
    assert result.success is True


@pytest.mark.asyncio
async def test_failed_delivery_calls_submit_result_with_failure():
    """A failing deliver_fn should submit_result(success=False) with error."""
    w = _FakeChannelWorker.__new__(_FakeChannelWorker)
    w.channel_name = "fake"
    w.poll_seconds = 0.01
    w._stopping = False
    w._last_poll_at = None
    w._last_success_at = None
    w._last_error = None

    fake_job = DeliveryJob(channel="fake", payload={}, job_id="j2")
    w.bus = MagicMock()
    w.bus.delivery_job_board.claim = MagicMock(
        side_effect=[fake_job, None]
    )
    w.bus.delivery_job_board.submit_result = MagicMock()
    w.bus.delivery_job_board.pending_count = MagicMock(return_value=0)
    w.bus.settings_book.get = MagicMock(return_value=None)

    async def failing_deliver(job):
        raise RuntimeError("TG API timeout")

    # Run one iteration
    task = asyncio.create_task(w._claim_delivery_loop(failing_deliver, "fake"))
    await asyncio.sleep(0.05)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    w.bus.delivery_job_board.submit_result.assert_called()
    result = w.bus.delivery_job_board.submit_result.call_args.kwargs["result"]
    assert result.success is False
    assert "TG API timeout" in str(result.error)


@pytest.mark.asyncio
async def test_skips_job_with_wrong_channel():
    """A claimed job whose channel doesn't match should be released, not delivered."""
    delivered: list[DeliveryJob] = []

    w = _FakeChannelWorker.__new__(_FakeChannelWorker)
    w.channel_name = "fake"
    w.poll_seconds = 0.01
    w._stopping = False
    w._last_poll_at = None
    w._last_success_at = None
    w._last_error = None

    wrong_job = DeliveryJob(channel="tg", payload={}, job_id="j3")
    w.bus = MagicMock()
    w.bus.delivery_job_board.claim = MagicMock(
        side_effect=[wrong_job, None]
    )
    w.bus.delivery_job_board.release = MagicMock()
    w.bus.delivery_job_board.pending_count = MagicMock(return_value=0)
    w.bus.settings_book.get = MagicMock(return_value=None)

    async def deliver(job):
        delivered.append(job)

    task = asyncio.create_task(w._claim_delivery_loop(deliver, "fake"))
    await asyncio.sleep(0.05)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    assert len(delivered) == 0
    w.bus.delivery_job_board.release.assert_called()


@pytest.mark.asyncio
async def test_backpressure_throttle_skips_claim():
    """When pending_count exceeds max_depth, claim should not be called."""
    w = _FakeChannelWorker.__new__(_FakeChannelWorker)
    w.channel_name = "fake"
    w.poll_seconds = 0.01
    w._stopping = False
    w._last_poll_at = None
    w._last_success_at = None
    w._last_error = None

    w.bus = MagicMock()
    w.bus.delivery_job_board.claim = MagicMock()
    w.bus.delivery_job_board.pending_count = MagicMock(return_value=5000)
    w.bus.settings_book.get = MagicMock(return_value="10")  # max_depth=10

    async def deliver(job):
        pass

    task = asyncio.create_task(w._claim_delivery_loop(deliver, "fake"))
    await asyncio.sleep(0.10)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    # claim should NOT be called because depth > max
    w.bus.delivery_job_board.claim.assert_not_called()
