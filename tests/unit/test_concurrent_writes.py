"""§20.1 — SQLite concurrent writes do not deadlock.

The bus relies on at-least-once delivery + idempotent consumption
with multiple workers (AgentWorker, ToolWorker, DeliveryWorker)
hitting the same SQLite file. WAL + ``busy_timeout=5000`` +
``BEGIN IMMEDIATE`` together make this safe in practice; this
file asserts the property holds for the bus's typical hot-path
operations.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.db import init_orm


def _setup(tmp_path: Path, monkeypatch) -> BusStore:
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    init_orm(str(tmp_path / "memories"), seed_root=False)
    return BusStore(str(tmp_path))


def test_two_writers_to_same_db_do_not_deadlock(
    tmp_path: Path, monkeypatch
) -> None:
    """Two threads hammering ``enqueue_tool_job`` finish without SQLITE_BUSY."""
    store = _setup(tmp_path, monkeypatch)
    errors: list[Exception] = []
    iterations = [0, 0]

    def worker(thread_id: int, tool_call_id_prefix: str) -> None:
        try:
            for i in range(40):
                job_id = store.enqueue_tool_job(
                    run_id=f"run-{thread_id}",
                    tool_call_id=f"{tool_call_id_prefix}-{i}",
                    tool_name="fake_tool",
                    arguments={"i": i},
                    context={"workspace": str(tmp_path)},
                )
                iterations[thread_id] += 1
                assert job_id
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(0, "a")),
        threading.Thread(target=worker, args=(1, "b")),
    ]
    deadline = time.time() + 15
    for t in threads:
        t.start()
    for t in threads:
        remaining = max(0.0, deadline - time.time())
        t.join(remaining)

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    assert iterations == [40, 40]


def test_concurrent_publish_agent_message_preserves_idempotency(
    tmp_path: Path, monkeypatch
) -> None:
    """Two threads publishing the same ``event_id`` produce one inbox row."""
    from magi.bus.db import open_session
    from magi.bus.db.models.queue import AgentInbox

    store = _setup(tmp_path, monkeypatch)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(30):
                store.publish_agent_message(AgentMessage(
                    event_id="shared-event",
                    text="hello",
                    channel="test",
                ))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)

    assert not errors, f"publish raised: {errors[:3]}"
    with open_session() as session:
        count = session.query(AgentInbox).filter(
            AgentInbox.event_id == "shared-event"
        ).count()
    assert count == 1