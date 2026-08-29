"""Contract tests for the startup-owned worker lifecycle."""

from __future__ import annotations

import asyncio
import time

import pytest

from runtime_worker import RuntimeWorker


class _ProbeWorker(RuntimeWorker):
    worker_name = "probe"

    def __init__(self, *, concurrency: int | None = None) -> None:
        super().__init__(bus=None, concurrency=concurrency)  # type: ignore[arg-type]
        self.started = asyncio.Event()

    async def _run(self) -> None:
        self.started.set()
        while not self._stopping:
            await asyncio.sleep(0.01)


class _SkippedWorker(_ProbeWorker):
    async def on_start(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_runtime_worker_lifecycle_children_and_health() -> None:
    worker = _ProbeWorker()
    await worker.start()
    first_task = worker._task
    await worker.start()  # idempotent
    assert worker._task is first_task
    await worker.started.wait()

    child_cancelled = asyncio.Event()

    async def child() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            child_cancelled.set()
            raise

    worker.spawn(child(), name="probe-child")
    assert worker.health()["running"] is True
    assert worker.health()["inflight"] == 1
    await worker.stop()
    assert child_cancelled.is_set()
    assert worker.health()["running"] is False


@pytest.mark.asyncio
async def test_call_keeps_event_loop_responsive() -> None:
    worker = _ProbeWorker()
    ticked = asyncio.Event()

    async def tick() -> None:
        await asyncio.sleep(0.01)
        ticked.set()

    def blocking() -> str:
        time.sleep(0.05)
        return "done"

    tick_task = asyncio.create_task(tick())
    assert await worker.call(blocking) == "done"
    await tick_task
    assert ticked.is_set()


@pytest.mark.asyncio
async def test_worker_can_intentionally_skip_startup() -> None:
    worker = _SkippedWorker()
    assert await worker.start() is False
    assert worker._task is None
    assert worker.health()["running"] is False


@pytest.mark.asyncio
async def test_reserved_capacity_limits_managed_jobs() -> None:
    worker = _ProbeWorker(concurrency=1)
    running = asyncio.Event()
    finish = asyncio.Event()

    async def job() -> None:
        running.set()
        await finish.wait()

    await worker.reserve_capacity()
    task = worker.spawn_reserved(job())
    await running.wait()

    next_reservation = asyncio.create_task(worker.reserve_capacity())
    await asyncio.sleep(0)
    assert not next_reservation.done()

    finish.set()
    await task
    await next_reservation
    worker.release_capacity()
