"""Concurrent-claim tests — verify the CAS pattern holds under SQLite WAL.

The 2026-08-10 architecture review (P1 #1) flagged the previous
``SELECT ... FOR UPDATE SKIP LOCKED`` claim path as unsafe on
SQLite, which silently no-ops the lock under WAL. The fix is
``_cas_claim``: find candidate → conditional UPDATE → check
``rowcount``. These tests run two consumers concurrently against
the same board to verify that:

1. **No double-claim.** Each row is owned by exactly one worker
   at any moment — never both.
2. **Delivery channel isolation.** Workers reading different
   channels never receive each other's rows.
3. **Lease recovery.** A worker whose lease expired (we
   ``UPDATE leased_until`` to the past) is reclaimed exactly once
   by the next consumer.

The tests use ``threading`` rather than asyncio because the CAS
claim path is purely synchronous (it calls SQLAlchemy sync APIs
under the hood, with workers wrapping in ``asyncio.to_thread``
at the worker layer). Spawning threads lets us overlap claims
in wall-clock time without having to coordinate event loops.

The boards used here (``runTaskJobBoard``, ``deliveryNotifyJobBoard``)
are the ones reviewed in the architecture review; each test
isolates the invariant it cares about so a regression points
straight at the broken layer.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.bases.job import JobStatus
from magi.old_bus.firmwares.jobs.deliveryNotifyJob import (
    DeliveryNotifyJob,
    DeliveryNotifyResult,
    deliveryNotifyJobBoard,
)
from magi.old_bus.firmwares.jobs.runTaskJob import (
    RunTaskJob,
    runTaskJobBoard,
)


def _fresh_factory(tmp_path: Path) -> EngineFactory:
    """File-backed SQLite shared by all worker-thread connections."""
    f = EngineFactory(f"sqlite:///{tmp_path / 'jobs.db'}")
    f.create_all()
    return f


def _seed_run_tasks(f, *, count: int) -> list[str]:
    """Insert N pending RunTaskJobs; return their job_ids."""
    board = runTaskJobBoard(f)
    ids = [board.publish(RunTaskJob(task_id=f"t_{i}")) for i in range(count)]
    return ids


def _seed_delivery(f, *, channel: str, count: int) -> list[str]:
    """Insert N pending DeliveryNotifyJobs for *channel*; return job_ids."""
    board = deliveryNotifyJobBoard(f)
    ids = [
        board.publish(DeliveryNotifyJob(channel=channel, text=f"#{i}", destination="x"))
        for i in range(count)
    ]
    return ids


# -- 1. No double-claim on the generic board ------------------------------


@pytest.mark.parametrize("thread_count", [2, 4, 8])
def test_run_task_no_double_claim_across_threads(tmp_path: Path, thread_count: int) -> None:
    """N threads concurrently claim from the same pool.

    Each row must be claimed by exactly one thread; the union of
    claimed ids must equal the set of seeded ids.
    """
    f = _fresh_factory(tmp_path)
    seeded_ids = set(_seed_run_tasks(f, count=64))

    # Each thread opens its own session-bound board (they share
    # the engine, not the session — SQLAlchemy sessions are
    # not thread-safe).
    barrier = threading.Barrier(thread_count)
    claimed_per_thread: list[set[str]] = []
    lock = threading.Lock()

    def worker() -> None:
        board = runTaskJobBoard(f)
        worker_id = f"run-task-{threading.get_ident()}"
        own: set[str] = set()
        # Barrier so all threads start polling simultaneously.
        barrier.wait()
        while True:
            job = board.claim(worker_id=worker_id)
            if job is None:
                break
            own.add(job.job_id)
            # Submit the result so the row doesn't sit as
            # "processing" forever and confuse subsequent
            # attempts. ``_invoke_safe`` semantics: submit
            # success.
            from magi.old_bus.firmwares.jobs.runTaskJob import RunTaskResult

            board.submit_result(
                job_id=job.job_id,
                worker_id=worker_id,
                result=RunTaskResult(job_id=job.job_id, status=JobStatus.COMPLETED),
            )
        with lock:
            claimed_per_thread.append(own)

    with ThreadPoolExecutor(max_workers=thread_count) as pool:
        futures = [pool.submit(worker) for _ in range(thread_count)]
        for f_ in as_completed(futures):
            f_.result()

    # Property 1: total claimed == total seeded (no rows lost).
    total_claimed = set().union(*claimed_per_thread)
    assert total_claimed == seeded_ids, f"rows claimed = {total_claimed}, expected = {seeded_ids}"

    # Property 2: no double-claim — each row owned by exactly one
    # thread.
    seen: dict[str, int] = {}
    for own in claimed_per_thread:
        for jid in own:
            seen[jid] = seen.get(jid, 0) + 1
    duplicates = {jid: n for jid, n in seen.items() if n > 1}
    assert not duplicates, f"rows claimed by multiple threads: {duplicates}"


# -- 2. Delivery channel isolation ---------------------------------------


def test_delivery_channel_workers_do_not_steal_each_other_rows(tmp_path: Path) -> None:
    """Two threads, two channels — each thread sees only its own slice."""
    f = _fresh_factory(tmp_path)
    tg_ids = set(_seed_delivery(f, channel="tg", count=20))
    webui_ids = set(_seed_delivery(f, channel="webui", count=20))

    barrier = threading.Barrier(2)
    tg_seen: set[str] = set()
    webui_seen: set[str] = set()
    lock = threading.Lock()

    def drain(channel: str, sink: list[set[str]]) -> None:
        board = deliveryNotifyJobBoard(f)
        worker_id = f"delivery-{channel}-{threading.get_ident()}"
        own: set[str] = set()
        barrier.wait()
        while True:
            job = board.claim_for_channel(channel=channel, worker_id=worker_id)
            if job is None:
                break
            own.add(job.job_id)
            board.submit_result(
                job_id=job.job_id,
                worker_id=worker_id,
                result=DeliveryNotifyResult(job_id=job.job_id, status=JobStatus.COMPLETED),
            )
        with lock:
            sink.append(own)

    sink_a: list[set[str]] = []
    sink_b: list[set[str]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(drain, "tg", sink_a)
        f2 = pool.submit(drain, "webui", sink_b)
        f1.result()
        f2.result()

    tg_seen = sink_a[0]
    webui_seen = sink_b[0]
    # Property 1: tg thread only saw tg rows.
    assert tg_seen <= tg_ids, f"tg thread claimed non-tg rows: {tg_seen - tg_ids}"
    # Property 2: webui thread only saw webui rows.
    assert webui_seen <= webui_ids, f"webui thread claimed non-webui rows: {webui_seen - webui_ids}"
    # Property 3: every seeded row was claimed by its channel.
    assert tg_seen == tg_ids, f"tg rows missed: {tg_ids - tg_seen}"
    assert webui_seen == webui_ids, f"webui rows missed: {webui_ids - webui_seen}"


# -- 3. Lease recovery under contention ----------------------------------


def test_expired_lease_is_reclaimed_exactly_once(tmp_path: Path) -> None:
    """A row whose lease is forced into the past must be reclaimed by
    *one* of N concurrent consumers, not N copies of itself.

    The conditional UPDATE is the lock: only one consumer may replace the
    expired lease owner, while every loser observes no claim and polls later.
    """
    f = _fresh_factory(tmp_path)
    board = runTaskJobBoard(f)
    jid = board.publish(RunTaskJob(task_id="lease_recovery"))

    # Force the lease into the past so the row qualifies for
    # reclaim.
    from datetime import timedelta

    from sqlalchemy import select, update

    from magi.old_bus.bases.db.base import utcnow_naive
    from magi.old_bus.firmwares.jobs.runTaskJob import _RunTaskJobRow

    with board._session() as s:
        s.execute(
            update(_RunTaskJobRow)
            .where(_RunTaskJobRow.job_id == jid)
            .values(
                status="processing",
                leased_until=utcnow_naive() - timedelta(seconds=10),
                leased_by="abandoned-worker",
            )
        )
        s.commit()

    # Now race N consumers against the stale lease.
    thread_count = 6
    barrier = threading.Barrier(thread_count)
    claimed_count = 0
    owners_after: list[str | None] = []
    lock = threading.Lock()

    def worker() -> None:
        nonlocal claimed_count
        b = runTaskJobBoard(f)
        worker_id = f"lease-{threading.get_ident()}"
        barrier.wait()
        job = b.claim(worker_id=worker_id)
        if job is not None and job.job_id == jid:
            with lock:
                claimed_count += 1
        # All threads inspect the durable owner to assert it changed once.
        with b._session() as s:
            row = s.scalar(select(_RunTaskJobRow).where(_RunTaskJobRow.job_id == jid))
            if row is not None:
                with lock:
                    owners_after.append(row.leased_by)

    with ThreadPoolExecutor(max_workers=thread_count) as pool:
        futures = [pool.submit(worker) for _ in range(thread_count)]
        for f_ in as_completed(futures):
            f_.result()

    # Exactly one thread should have claimed the row.
    assert claimed_count == 1, f"expected 1 claim, got {claimed_count}"
    assert owners_after, "no thread observed lease owner; query bug"
    assert len(set(owners_after)) == 1
    assert owners_after[0] != "abandoned-worker"
