"""TaskWorker smoke tests — rebased from old TaskScheduler tests.

Tests TaskWorker.__init__, start/stop lifecycle, and cron fire logic.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from unittest.mock import MagicMock

from magi.channels.tasks.worker import TaskWorker


def test_init_populates_required_attributes():
    """TaskWorker.__init__ should set expected internal state."""
    mock_bus = MagicMock()
    mock_bus.tasks_book.list_all_enabled_for_workers = MagicMock(return_value=[])
    mock_bus.task_runs_book.reap_stale = MagicMock(return_value=0)
    mock_bus.run_task_job_board = MagicMock()
    mock_bus.agent_job_board = MagicMock()
    mock_bus.messages_book = MagicMock()

    w = TaskWorker(mock_bus)
    assert w.worker_name == "task"
    assert w.worker_kind == "scheduler"
    assert w._stopping is False
    assert w._task is None
    assert isinstance(w._next_fire, dict)
    assert w._rehydrated is False


def test_startup_registers_task_channel():
    mock_bus = MagicMock()
    w = TaskWorker(mock_bus)

    asyncio.run(w.on_start())

    mock_bus.settings_book.register_channel.assert_called_once_with(name="task")


def test_should_fire_cron_coalesce_equivalent():
    """_should_fire_cron fires at most once per missed cron window."""
    from dataclasses import dataclass
    from datetime import datetime

    @dataclass
    class FakeTask:
        task_id: str = "t1"
        cron: str = "0 * * * *"  # every hour at :00
        run_at: datetime | None = None
        enabled: int = 1

    mock_bus = MagicMock()
    mock_bus.tasks_book.list_all_enabled_for_workers = MagicMock(return_value=[])
    mock_bus.task_runs_book.reap_stale = MagicMock(return_value=0)
    mock_bus.run_task_job_board = MagicMock()
    mock_bus.agent_job_board = MagicMock()
    mock_bus.messages_book = MagicMock()

    w = TaskWorker(mock_bus)
    task = FakeTask()
    now = datetime.now(UTC)

    # First time: should fire (no last_fire recorded)
    assert w._should_fire(task, now) is True

    # Record a fire
    w._next_fire[task.task_id] = now.replace(tzinfo=None)

    # Immediately after: should NOT fire again (coalesce)
    assert w._should_fire(task, now) is False
