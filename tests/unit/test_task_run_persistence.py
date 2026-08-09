"""Unit tests for TaskBook persistence methods added for TaskWorker.

Tests: record_run_start, mark_run_at_consumed, list_all_enabled_for_workers,
and TaskRunBook.reap_stale.
"""

from __future__ import annotations

import pytest

from magi.bus.db import EngineFactory
from magi.bus.library.local.tasksBook import (
    ChannelEnum,
    TaskBook,
    TaskRunBook,
    SOURCE_USER,
)


@pytest.fixture
def factory():
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


@pytest.fixture
def task_book(factory):
    return TaskBook(factory)


@pytest.fixture
def task_run_book(factory):
    return TaskRunBook(factory)


def _make_test_task(task_book, task_id="task_test1", cron="0 9 * * *"):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    # Use the TaskBook's add with valid schedule
    return task_book.add(
        name=f"Test Task {task_id}",
        prompt="Do nothing",
        cron=cron,
        target_channel=ChannelEnum.WEBUI,
        uid=42,
        session_id="sess_01",
        tz="UTC",
        created_at=now,
        updated_at=now,
    )


class TestRecordRunStart:
    def test_creates_task_run_and_updates_last_run_at(self, task_book, task_run_book):
        task = _make_test_task(task_book, "task_rt1")
        run = task_book.record_run_start(
            task_id=task.id, trigger="cron_tick",
        )
        assert run is not None
        assert run.task_id == task.id
        assert run.trigger == "cron_tick"
        assert run.status == "running"

        # Verify task.last_run_at was updated
        updated = task_book.get(task_id=task.id)
        assert updated is not None
        assert updated.last_run_at is not None

    def test_run_id_can_be_provided(self, task_book):
        task = _make_test_task(task_book, "task_rt2")
        run = task_book.record_run_start(
            task_id=task.id, trigger="manual_run", run_id="my_run_42",
        )
        assert run.id == "my_run_42"


class TestMarkRunAtConsumed:
    def test_sets_enabled_to_zero(self, task_book):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        task = task_book.add(
            name="One-shot Task",
            prompt="Run once",
            run_at=(datetime.now(timezone.utc).isoformat() + "Z").replace(
                "+00:00", "Z"
            ),
            target_channel=ChannelEnum.WEBUI,
            uid=42,
            session_id="sess_os",
            tz="UTC",
            created_at=now,
            updated_at=now,
        )
        assert task.enabled == 1

        task_book.mark_run_at_consumed(task_id=task.id)
        updated = task_book.get(task_id=task.id)
        assert updated is not None
        assert updated.enabled == 0


class TestListAllEnabledForWorkers:
    def test_lists_enabled_user_tasks_across_uids(self, task_book):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        # Task for uid 42
        task_book.add(
            name="User 42 Task",
            prompt="do stuff",
            cron="0 9 * * *",
            target_channel=ChannelEnum.WEBUI,
            uid=42,
            session_id="sess_42",
            tz="UTC",
            created_at=now,
            updated_at=now,
        )
        # Task for uid 99
        task_book.add(
            name="User 99 Task",
            prompt="do other stuff",
            cron="*/30 * * * *",
            target_channel=ChannelEnum.TG,
            uid=99,
            session_id="sess_99",
            tz="UTC",
            created_at=now,
            updated_at=now,
        )

        tasks = task_book.list_all_enabled_for_workers()
        assert len(tasks) == 2
        uids = {t.uid for t in tasks}
        assert 42 in uids
        assert 99 in uids

    def test_excludes_disabled_tasks(self, task_book):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        t = task_book.add(
            name="Disabled Task",
            prompt="skip",
            cron="0 9 * * *",
            target_channel=ChannelEnum.WEBUI,
            uid=42,
            session_id="sess_d",
            tz="UTC",
            created_at=now,
            updated_at=now,
        )
        task_book.disable(task_id=t.id, uid=42)

        tasks = task_book.list_all_enabled_for_workers()
        task_ids = {t.id for t in tasks}
        assert t.id not in task_ids


class TestReapStale:
    def test_flips_stuck_running_rows_to_failed(self, task_book, task_run_book):
        task = _make_test_task(task_book, "task_stale")
        run = task_book.record_run_start(task_id=task.id, trigger="cron_tick")

        # Simulate stale by backdating started_at
        from datetime import datetime, timedelta, timezone

        stale_time = (
            datetime.now(timezone.utc) - timedelta(seconds=600)
        ).isoformat()
        from magi.bus.library.local.tasksBook import _TaskRunRow
        from sqlalchemy import select

        with task_run_book._session() as s:
            row = s.scalar(
                select(_TaskRunRow).where(_TaskRunRow.id == run.id)
            )
            if row:
                row.started_at = stale_time
                s.commit()

        n = task_run_book.reap_stale(older_than_seconds=300)
        assert n == 1

        reaped = task_run_book.get(run_id=run.id)
        assert reaped is not None
        assert reaped.status == "failed"
        assert reaped.error == "abandoned by previous worker"

    def test_ignores_recent_running_rows(self, task_book, task_run_book):
        task = _make_test_task(task_book, "task_recent")
        task_book.record_run_start(task_id=task.id, trigger="cron_tick")

        n = task_run_book.reap_stale(older_than_seconds=300)
        assert n == 0  # should be too recent
