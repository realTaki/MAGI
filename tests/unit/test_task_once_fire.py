"""Regression tests for ``frequency="once"`` task path — rebased to bus.

validate_run_at / validate_run_at_future tests kept from original.
Scheduler tests rewritten for TaskWorker + RunTaskJob flow.
"""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timedelta

import pytest

from magi.old_bus.firmwares.books.local.contactBook import Contact, Role
from magi.old_bus.firmwares.books.local.tasksBook import (
    Task,
    validate_run_at,
    validate_run_at_future,
)

# -- validate_run_at --------------------------------------------------------


def test_validate_run_at_accepts_offset_aware_iso() -> None:
    raw = "2026-08-01T15:30:00+08:00"
    out = validate_run_at(raw)
    assert out == dt.datetime(2026, 8, 1, 7, 30)


def test_validate_run_at_naive_iso_treated_as_utc() -> None:
    raw = "2026-08-01T15:30:00"
    out = validate_run_at(raw)
    assert out == dt.datetime(2026, 8, 1, 15, 30)


def test_validate_run_at_rejects_empty_garbage() -> None:
    for bad in ("", "  ", "2026-13-40", "not-a-date", "2026/08/01"):
        with pytest.raises(ValueError):
            validate_run_at(bad)


def test_validate_run_at_normalises_whitespace() -> None:
    # ``validate_run_at`` trims whitespace and normalises to naive UTC.
    out = validate_run_at("  2026-08-01T15:30:00+08:00  ")
    assert out == dt.datetime(2026, 8, 1, 7, 30)


# -- TaskWorker run_at consumption ------------------------------------------


def test_worker_should_fire_run_at_once():
    """TaskWorker._should_fire fires a run_at task exactly once."""
    from dataclasses import dataclass
    from unittest.mock import MagicMock

    @dataclass
    class FakeTask:
        task_id: str = "t_runat"
        cron: str | None = None
        run_at: datetime | None = None
        enabled: int = 1

    mock_bus = MagicMock()
    mock_bus.tasks_book.list_all_enabled_for_workers = MagicMock(return_value=[])
    mock_bus.task_runs_book.reap_stale = MagicMock(return_value=0)
    mock_bus.run_task_job_board = MagicMock()
    mock_bus.agent_job_board = MagicMock()
    mock_bus.messages_book = MagicMock()

    from magi.channels.tasks.worker import TaskWorker

    w = TaskWorker(mock_bus)

    past = (datetime.now(dt.UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    task = FakeTask(run_at=past)

    # First time: should fire
    assert w._should_fire(task, datetime.now(dt.UTC)) is True

    # Record a fire
    w._next_fire[task.task_id] = datetime.now(dt.UTC).replace(tzinfo=None)

    # Second time: should NOT fire (already fired)
    assert w._should_fire(task, datetime.now(dt.UTC)) is False


def test_mark_run_at_consumed_sets_enabled_zero():
    """TaskBook.mark_run_at_consumed sets enabled=0 after fire."""
    from magi.old_bus.bases.db import EngineFactory
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook
    from magi.old_bus.firmwares.books.local.conversationBook import ConversationBook  # noqa: F401
    from magi.old_bus.firmwares.books.local.tasksBook import TaskBook

    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    tb = TaskBook(f)

    # ``tasks.contact_id`` → ``contacts.id`` is a RESTRICT FK; seed a
    # contact so the INSERT below doesn't trip it.
    contact_id = ContactBook(f).get(ContactBook(f).add(Contact(name='test-contact', role=Role.ASSIGNED))).id

    future = (datetime.now(dt.UTC) + timedelta(hours=1)).replace(tzinfo=None)
    datetime.now(dt.UTC).replace(tzinfo=None)

    task = tb.get(tb.add(Task(name='Once consume test', prompt='run once then disable', run_at=future, target_channel="webui", contact_id=contact_id, conversation_id=None, tz='UTC')))
    assert task.enabled == 1

    tb.mark_run_at_consumed(task_id=task.task_id)
    updated = tb.get_by_task_id(task_id=task.task_id)
    assert updated is not None
    assert updated.enabled == 0


# -- validate_run_at_future --------------------------------------------------


def test_validate_run_at_future_accepts_clear_future() -> None:
    out = validate_run_at_future(datetime(2099, 1, 1))
    assert out == datetime(2099, 1, 1)


def test_validate_run_at_future_rejects_clear_past() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_run_at_future(datetime(2020, 1, 1))
    assert "in the future" in str(exc_info.value)


def test_validate_run_at_future_respects_grace_window() -> None:
    server_now = datetime.now(dt.UTC).replace(tzinfo=None)
    near_past = server_now - timedelta(seconds=30)
    validate_run_at_future(near_past)
    far_past = server_now - timedelta(seconds=90)
    with pytest.raises(ValueError):
        validate_run_at_future(far_past)


def test_validate_run_at_future_uses_explicit_now() -> None:
    fixed_now = datetime(2099, 6, 1, 12, 0, 0)
    past = fixed_now - timedelta(minutes=5)
    with pytest.raises(ValueError):
        validate_run_at_future(past, now=fixed_now)
    future = fixed_now + timedelta(days=1)
    validate_run_at_future(future, now=fixed_now)


def test_validate_run_at_future_handles_naive_input() -> None:
    server_now = datetime.now(dt.UTC).replace(tzinfo=None)
    naive_future = server_now + timedelta(hours=1)
    out = validate_run_at_future(naive_future)
    assert out == naive_future
