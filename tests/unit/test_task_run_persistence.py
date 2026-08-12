"""Unit tests for TaskBook persistence methods added for TaskWorker.

Tests: record_run_start, mark_run_at_consumed, list_all_enabled_for_workers,
and TaskRunBook.reap_stale.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from magi.bus.db import EngineFactory
from magi.bus.library.local.contactBook import Role
from magi.bus.library.local.tasksBook import (
    ChannelEnum,
    TaskBook,
    TaskRunBook,
)


@pytest.fixture
def factory():
    # Import every Book that registers an inline ORM model so
    # ``EngineFactory.create_all`` lays down the whole schema —
    # otherwise the FKs on ``tasks`` (chat_conversations, contacts) are
    # left dangling and the INSERT below fails.
    from magi.bus.library.local.contactBook import ContactBook  # noqa: F401
    from magi.bus.library.local.conversationBook import ConversationBook  # noqa: F401

    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


@pytest.fixture
def task_book(factory):
    return TaskBook(factory)


@pytest.fixture
def task_run_book(factory):
    return TaskRunBook(factory)


def _seed_contact(factory, *, name="test-contact", role: Role = Role.ASSIGNED) -> int:
    """Ensure exactly one contact row exists; return its ``id``.

    Each test gets a fresh in-memory SQLite so contacts added in one
    test don't exist in another. This helper guarantees an FK target
    is present whenever a test needs ``tasks.uid``.
    """
    from magi.bus.library.local.contactBook import ContactBook

    cbook = ContactBook(factory)
    existing = cbook.list_all()
    if existing:
        return existing[0].id
    return cbook.add(name=name, role=role).id


def _make_test_task(task_book, factory, task_id="task_test1", cron="0 9 * * *"):
    from datetime import datetime

    uid = _seed_contact(factory)

    now = datetime.now(UTC).isoformat()
    # Use the TaskBook's add with valid schedule. ``conversation_id``
    # is None so we don't trip the FK to ``chat_conversations`` — the
    # session-creation flow is exercised by chat tests, not here.
    return task_book.add(
        name=f"Test Task {task_id}",
        prompt="Do nothing",
        cron=cron,
        target_channel=ChannelEnum.WEBUI,
        contact_id=uid,
        conversation_id=None,
        tz="UTC",
        created_at=now,
        updated_at=now,
    )


class TestRecordRunStart:
    def test_creates_task_run_and_updates_last_run_at(self, task_book, task_run_book):
        _ = task_run_book
        task = _make_test_task(task_book, task_book._factory, "task_rt1")
        run = task_book.record_run_start(
            task_id=task.id,
            manual=False,
        )
        assert run is not None
        assert run.task_id == task.id
        assert run.manual is False
        assert run.status == "running"

        # Verify task.last_run_at was updated
        updated = task_book.get(task_id=task.id)
        assert updated is not None
        assert updated.last_run_at is not None

    def test_run_id_can_be_provided(self, task_book):
        task = _make_test_task(task_book, task_book._factory, "task_rt2")
        run = task_book.record_run_start(
            task_id=task.id,
            manual=True,
            id="my_run_42",
        )
        assert run.id == "my_run_42"


class TestMarkRunAtConsumed:
    def test_sets_enabled_to_zero(self, task_book, factory):
        from datetime import datetime

        # Use the contact id minted by the factory, not a hardcoded 42.
        uid = _seed_contact(factory)

        now = datetime.now(UTC).isoformat()
        # ``datetime.now(timezone.utc).isoformat()`` already returns
        # the trailing ``+00:00`` form; append ``Z`` directly so we
        # don't end up with ``...ZZ`` (which ISO parsing rejects).
        from datetime import datetime as _dt

        future_iso = _dt.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        task = task_book.add(
            name="One-shot Task",
            prompt="Run once",
            run_at=future_iso,
            target_channel=ChannelEnum.WEBUI,
            contact_id=uid,
            conversation_id=None,
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
    def test_lists_enabled_user_tasks_across_uids(self, task_book, factory):
        from datetime import datetime

        from magi.bus.library.local.contactBook import ContactBook

        # Two contacts so the test can assert both uids appear in
        # the worker-visible list.
        cbook = ContactBook(factory)
        cbook.add(name="contact-A", role=Role.ASSIGNED)
        cbook.add(name="contact-B", role=Role.ASSIGNED)
        contacts = cbook.list_all()
        uid_a, uid_b = contacts[0].id, contacts[1].id

        now = datetime.now(UTC).isoformat()
        task_book.add(
            name="User A Task",
            prompt="do stuff",
            cron="0 9 * * *",
            target_channel=ChannelEnum.WEBUI,
            contact_id=uid_a,
            conversation_id=None,
            tz="UTC",
            created_at=now,
            updated_at=now,
        )
        task_book.add(
            name="User B Task",
            prompt="do other stuff",
            cron="*/30 * * * *",
            target_channel=ChannelEnum.TG,
            contact_id=uid_b,
            conversation_id=None,
            tz="UTC",
            created_at=now,
            updated_at=now,
        )

        tasks = task_book.list_all_enabled_for_workers()
        assert len(tasks) == 2
        uids = {t.contact_id for t in tasks}
        assert uid_a in uids
        assert uid_b in uids

    def test_excludes_disabled_tasks(self, task_book, factory):
        from datetime import datetime

        uid = _seed_contact(factory)

        now = datetime.now(UTC).isoformat()
        t = task_book.add(
            name="Disabled Task",
            prompt="skip",
            cron="0 9 * * *",
            target_channel=ChannelEnum.WEBUI,
            contact_id=uid,
            conversation_id=None,
            tz="UTC",
            created_at=now,
            updated_at=now,
        )
        task_book.disable(task_id=t.id, contact_id=uid)

        tasks = task_book.list_all_enabled_for_workers()
        task_ids = {t.id for t in tasks}
        assert t.id not in task_ids


class TestReapStale:
    def test_flips_stuck_running_rows_to_failed(self, task_book, task_run_book):
        task = _make_test_task(task_book, task_book._factory, "task_stale")
        run = task_book.record_run_start(task_id=task.id, manual=False)

        # Simulate stale by backdating started_at
        from datetime import datetime, timedelta

        stale_time = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
        from sqlalchemy import select

        from magi.bus.library.local.tasksBook import _TaskRunRow

        with task_run_book._session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.id == run.id))
            if row:
                row.started_at = stale_time
                s.commit()

        n = task_run_book.reap_stale(older_than_seconds=300)
        assert n == 1

        reaped = task_run_book.get(id=run.id)
        assert reaped is not None
        assert reaped.status == "failed"
        assert reaped.error == "abandoned by previous worker"

    def test_ignores_recent_running_rows(self, task_book, task_run_book):
        task = _make_test_task(task_book, task_book._factory, "task_recent")
        task_book.record_run_start(task_id=task.id, manual=False)

        n = task_run_book.reap_stale(older_than_seconds=300)
        assert n == 0  # should be too recent
