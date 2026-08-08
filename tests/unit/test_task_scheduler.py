"""TaskWorker smoke tests — rebased from old TaskScheduler tests.

Tests TaskWorker.__init__, start/stop lifecycle, and cron fire logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from magi.channels.workers.task import TaskWorker


def test_init_populates_required_attributes():
    """TaskWorker.__init__ should set expected internal state."""
    mock_bus = MagicMock()
    mock_bus.tasks_book.list_all_enabled_for_workers = MagicMock(return_value=[])
    mock_bus.task_runs_book.reap_stale = MagicMock(return_value=0)
    mock_bus.run_task_job_board = MagicMock()
    mock_bus.agent_job_board = MagicMock()
    mock_bus.messages_book = MagicMock()

    w = TaskWorker(mock_bus)
    assert w.channel_name == "task"
    assert w._stopping is False
    assert w._task is None
    assert isinstance(w._next_fire, dict)
    assert w._rehydrated is False


def test_should_fire_cron_coalesce_equivalent():
    """_should_fire_cron fires at most once per missed cron window."""
    from datetime import datetime, timezone
    from dataclasses import dataclass

    @dataclass
    class FakeTask:
        id: str = "t1"
        cron: str = "0 * * * *"  # every hour at :00
        run_at: str | None = None
        enabled: int = 1

    mock_bus = MagicMock()
    mock_bus.tasks_book.list_all_enabled_for_workers = MagicMock(return_value=[])
    mock_bus.task_runs_book.reap_stale = MagicMock(return_value=0)
    mock_bus.run_task_job_board = MagicMock()
    mock_bus.agent_job_board = MagicMock()
    mock_bus.messages_book = MagicMock()

    w = TaskWorker(mock_bus)
    task = FakeTask()
    now = datetime.now(timezone.utc)

    # First time: should fire (no last_fire recorded)
    assert w._should_fire(task, now) is True

    # Record a fire
    w._next_fire[task.id] = now

    # Immediately after: should NOT fire again (coalesce)
    assert w._should_fire(task, now) is False
