"""Regression tests for ``frequency="once"`` task path — rebased to bus.

validate_run_at / validate_run_at_future tests kept from original.
Scheduler tests rewritten for TaskWorker + RunTaskJob flow.
"""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timedelta, timezone

import pytest

from magi.bus.library.local.tasksBook import (
    validate_run_at,
    validate_run_at_future,
)


# -- validate_run_at --------------------------------------------------------

def test_validate_run_at_accepts_offset_aware_iso() -> None:
    raw = "2026-08-01T15:30:00+08:00"
    out = validate_run_at(raw)
    # v3 contract: canonicalise to the UTC trailing-Z form so two
    # operators writing the same instant in different offsets collapse
    # to the same row.
    assert out == "2026-08-01T07:30:00Z"
    assert dt.datetime.fromisoformat(out).astimezone(dt.timezone.utc) == \
        dt.datetime(2026, 8, 1, 7, 30, tzinfo=dt.timezone.utc)


def test_validate_run_at_naive_iso_treated_as_utc() -> None:
    raw = "2026-08-01T15:30:00"
    out = validate_run_at(raw)
    parsed = dt.datetime.fromisoformat(out)
    assert parsed.tzinfo is not None
    assert parsed.astimezone(dt.timezone.utc) == \
        dt.datetime(2026, 8, 1, 15, 30, tzinfo=dt.timezone.utc)


def test_validate_run_at_rejects_empty_garbage() -> None:
    for bad in ("", "  ", "2026-13-40", "not-a-date", "2026/08/01"):
        with pytest.raises(ValueError):
            validate_run_at(bad)


def test_validate_run_at_normalises_whitespace() -> None:
    # ``validate_run_at`` trims whitespace AND canonicalises to the
    # UTC trailing-Z form (per the v3 contract — see the docstring
    # of ``validate_run_at`` in tasksBook.py). 15:30 in UTC+8 is
    # 07:30 UTC.
    out = validate_run_at("  2026-08-01T15:30:00+08:00  ")
    assert out == "2026-08-01T07:30:00Z"


# -- TaskWorker run_at consumption ------------------------------------------

def test_worker_should_fire_run_at_once():
    """TaskWorker._should_fire fires a run_at task exactly once."""
    from unittest.mock import MagicMock
    from dataclasses import dataclass

    @dataclass
    class FakeTask:
        id: str = "t_runat"
        cron: str | None = None
        run_at: str | None = None
        enabled: int = 1

    mock_bus = MagicMock()
    mock_bus.tasks_book.list_all_enabled_for_workers = MagicMock(return_value=[])
    mock_bus.task_runs_book.reap_stale = MagicMock(return_value=0)
    mock_bus.run_task_job_board = MagicMock()
    mock_bus.agent_job_board = MagicMock()
    mock_bus.messages_book = MagicMock()

    from magi.channels.tasks.worker import TaskWorker
    w = TaskWorker(mock_bus)

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    task = FakeTask(run_at=past)

    # First time: should fire
    assert w._should_fire(task, datetime.now(timezone.utc)) is True

    # Record a fire
    w._next_fire[task.id] = datetime.now(timezone.utc)

    # Second time: should NOT fire (already fired)
    assert w._should_fire(task, datetime.now(timezone.utc)) is False


def test_mark_run_at_consumed_sets_enabled_zero():
    """TaskBook.mark_run_at_consumed sets enabled=0 after fire."""
    from magi.bus.db import EngineFactory
    from magi.bus.library.local.tasksBook import TaskBook, ChannelEnum, SOURCE_USER
    from magi.bus.library.local.contactBook import ContactBook
    from magi.bus.library.local.sessionBook import SessionBook  # noqa: F401

    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    tb = TaskBook(f)

    # ``tasks.uid`` → ``contacts.id`` is a RESTRICT FK; seed a contact
    # so the INSERT below doesn't trip it.
    uid = ContactBook(f).add(name="test-contact", role="assigned").id

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    task = tb.add(
        name="Once consume test",
        prompt="run once then disable",
        run_at=future,
        target_channel=ChannelEnum.WEBUI,
        uid=uid,
        session_id=None,
        tz="UTC",
        created_at=now,
        updated_at=now,
    )
    assert task.enabled == 1

    tb.mark_run_at_consumed(task_id=task.id)
    updated = tb.get(task_id=task.id)
    assert updated is not None
    assert updated.enabled == 0


# -- validate_run_at_future --------------------------------------------------

def test_validate_run_at_future_accepts_clear_future() -> None:
    out = validate_run_at_future("2099-01-01T00:00:00+00:00")
    assert out == "2099-01-01T00:00:00+00:00"


def test_validate_run_at_future_rejects_clear_past() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_run_at_future("2020-01-01T00:00:00+00:00")
    assert "in the future" in str(exc_info.value)


def test_validate_run_at_future_respects_grace_window() -> None:
    server_now = datetime.now(timezone.utc)
    near_past = (server_now - timedelta(seconds=30)).isoformat(timespec="seconds")
    validate_run_at_future(near_past)
    far_past = (server_now - timedelta(seconds=90)).isoformat(timespec="seconds")
    with pytest.raises(ValueError):
        validate_run_at_future(far_past)


def test_validate_run_at_future_uses_explicit_now() -> None:
    fixed_now = datetime(2099, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    past = (fixed_now - timedelta(minutes=5)).isoformat(timespec="seconds")
    with pytest.raises(ValueError):
        validate_run_at_future(past, now=fixed_now)
    future = (fixed_now + timedelta(days=1)).isoformat(timespec="seconds")
    validate_run_at_future(future, now=fixed_now)


def test_validate_run_at_future_handles_naive_input() -> None:
    server_now = datetime.now(timezone.utc)
    naive_future = (server_now + timedelta(hours=1)).replace(
        tzinfo=None
    ).isoformat(timespec="seconds")
    out = validate_run_at_future(naive_future)
    assert out == naive_future
