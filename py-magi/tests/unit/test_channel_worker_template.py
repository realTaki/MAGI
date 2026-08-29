"""Unit tests for ChannelWorker base class _claim_delivery_loop template.

Tests the base class template method with a fake deliver_fn, verifying:
- claim_for_channel → deliver → submit_result(success=True) flow
- deliver_fn failure → submit_result(success=False) flow
- backpressure branch when pending_count exceeds max_depth

The mock ``claim_for_channel`` uses an *infinite* ``side_effect`` (via a
generator) rather than a fixed list: ``MagicMock(side_effect=[...])``
raises ``StopIteration`` once the list is exhausted, which
``asyncio.to_thread`` cannot propagate cleanly into the awaiting Future
— the task then hangs instead of exiting cleanly. ``_stopping = True``
then has no effect because the loop never reaches the next ``while``
check.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from magi.old_bus.bases.job import JobStatus
from magi.old_bus.firmwares.jobs.deliveryNotifyJob import DeliveryNotifyJob
from magi.channels.worker_base import ChannelWorker


class _FakeChannelWorker(ChannelWorker):
    """Minimal concrete ChannelWorker for testing the base class template."""

    channel_name = "fake"

    async def _run(self) -> None:
        await self._claim_delivery_loop(self._deliver, "fake")

    async def _deliver(self, job: DeliveryNotifyJob) -> None:
        pass  # overridden in tests


def _claim_sequence(*values: object) -> MagicMock:
    """A ``claim_for_channel`` mock that yields ``values[0], values[1], ..., None``
    forever — never raises StopIteration, so the loop exits cleanly when
    ``_stopping`` flips True. Uses ``itertools.chain(once(values), repeat(None))``
    so the trailing ``None`` repeat is genuinely infinite without
    consuming a finite iterable.
    """
    from itertools import chain, repeat

    seq = chain(iter(values), repeat(None))

    def _next(channel=None, worker_id=None):
        return next(seq)

    return MagicMock(side_effect=_next)


def _bare_worker() -> _FakeChannelWorker:
    """Build the minimal RuntimeWorker state needed by the loop template."""
    worker = _FakeChannelWorker.__new__(_FakeChannelWorker)
    worker.channel_name = "fake"
    worker.worker_id = "fake-worker"
    worker.poll_seconds = 0.01
    worker.concurrency = 2
    worker._slots = asyncio.Semaphore(worker.concurrency)
    worker._children = set()
    worker._stopping = False
    worker._last_poll_at = None
    worker._last_success_at = None
    worker._last_error = None
    return worker


@pytest.mark.asyncio
async def test_successful_delivery_calls_submit_result_with_success():
    """A successful deliver_fn should submit_result(success=True)."""
    delivered: list[DeliveryNotifyJob] = []

    w = _bare_worker()

    fake_job = DeliveryNotifyJob(channel="fake", text="hi")
    w.bus = MagicMock()
    w.bus.delivery_notify_job_board.claim_for_channel = _claim_sequence(fake_job)
    w.bus.delivery_notify_job_board.submit_result = MagicMock()
    w.bus.delivery_notify_job_board.pending_count = MagicMock(return_value=0)
    w.bus.settings_book.get_value = MagicMock(return_value=None)  # default depth

    async def deliver(job):
        delivered.append(job)

    w._deliver = deliver

    # Run one iteration then stop
    task = asyncio.create_task(w._claim_delivery_loop(w._deliver, "fake"))
    await asyncio.sleep(0.05)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    assert len(delivered) == 1
    w.bus.delivery_notify_job_board.submit_result.assert_called()
    call_args = w.bus.delivery_notify_job_board.submit_result.call_args
    result = call_args.kwargs["result"]
    assert result.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_failed_delivery_calls_submit_result_with_failure():
    """A failing deliver_fn should submit_result(success=False) with error."""
    w = _bare_worker()

    fake_job = DeliveryNotifyJob(channel="fake", text="x")
    w.bus = MagicMock()
    w.bus.delivery_notify_job_board.claim_for_channel = _claim_sequence(fake_job)
    w.bus.delivery_notify_job_board.submit_result = MagicMock()
    w.bus.delivery_notify_job_board.pending_count = MagicMock(return_value=0)
    w.bus.settings_book.get_value = MagicMock(return_value=None)

    async def failing_deliver(job):
        raise RuntimeError("TG API timeout")

    # Run one iteration
    task = asyncio.create_task(w._claim_delivery_loop(failing_deliver, "fake"))
    await asyncio.sleep(0.05)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    w.bus.delivery_notify_job_board.submit_result.assert_called()
    result = w.bus.delivery_notify_job_board.submit_result.call_args.kwargs["result"]
    assert result.status == JobStatus.FAILED
    assert "TG API timeout" in str(result.error)


@pytest.mark.asyncio
async def test_skips_job_with_wrong_channel():
    """A mismatched claimed job is not delivered or actively released."""
    delivered: list[DeliveryNotifyJob] = []

    w = _bare_worker()

    wrong_job = DeliveryNotifyJob(channel="tg", text="x")
    w.bus = MagicMock()
    w.bus.delivery_notify_job_board.claim_for_channel = _claim_sequence(wrong_job)
    w.bus.delivery_notify_job_board.pending_count = MagicMock(return_value=0)
    w.bus.settings_book.get_value = MagicMock(return_value=None)

    async def deliver(job):
        delivered.append(job)

    task = asyncio.create_task(w._claim_delivery_loop(deliver, "fake"))
    await asyncio.sleep(0.05)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    assert len(delivered) == 0
    w.bus.delivery_notify_job_board.submit_result.assert_not_called()


@pytest.mark.asyncio
async def test_backpressure_throttle_skips_claim():
    """When pending_count exceeds max_depth, claim should not be called."""
    w = _bare_worker()

    w.bus = MagicMock()
    # claim_for_channel would raise StopIteration if reached — backpressure branch
    # must short-circuit BEFORE this mock is called.
    w.bus.delivery_notify_job_board.claim_for_channel = MagicMock(
        side_effect=AssertionError("claim_for_channel should not be called under backpressure"),
    )
    w.bus.delivery_notify_job_board.pending_count = MagicMock(return_value=5000)
    w.bus.settings_book.get_value = MagicMock(return_value="10")  # max_depth=10

    async def deliver(job):
        pass

    task = asyncio.create_task(w._claim_delivery_loop(deliver, "fake"))
    await asyncio.sleep(0.10)
    w._stopping = True
    await asyncio.wait_for(task, timeout=2.0)

    # claim_for_channel should NOT be called because depth > max
    w.bus.delivery_notify_job_board.claim_for_channel.assert_not_called()
