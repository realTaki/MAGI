"""Unit tests for TaskBook persistence methods added for TaskWorker.

Tests: record_run_start, mark_run_at_consumed, list_all_enabled_for_workers,
and TaskRunBook.reap_stale.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.firmwares.books.local.contactBook import Contact, Role
from magi.old_bus.firmwares.books.local.tasksBook import Task, TaskBook, TaskRunBook


@pytest.fixture
def factory():
    # Import every Book that registers an inline ORM model so
    # ``EngineFactory.create_all`` lays down the whole schema —
    # otherwise the FKs on ``tasks`` (chat_conversations, contacts) are
    # left dangling and the INSERT below fails.
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook  # noqa: F401
    from magi.old_bus.firmwares.books.local.conversationBook import ConversationBook  # noqa: F401
    from magi.old_bus.firmwares.books.magis.membershipBook import MagisMembershipBook  # noqa: F401

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
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    cbook = ContactBook(factory)
    existing = cbook.list_all()
    if existing:
        return existing[0].id
    return cbook.get(cbook.add(Contact(name=name, role=role))).id


def _make_test_task(task_book, factory, task_id="task_test1", cron="0 9 * * *"):
    uid = _seed_contact(factory)

    datetime.now(UTC).replace(tzinfo=None)
    # Use the TaskBook's add with valid schedule. ``conversation_id``
    # is None so we don't trip the FK to ``chat_conversations`` — the
    # session-creation flow is exercised by chat tests, not here.
    return task_book.get(task_book.add(Task(name=f'Test Task {task_id}', prompt='Do nothing', cron=cron, target_channel="webui", contact_id=uid, conversation_id=None, tz='UTC')))


class TestRecordRunStart:
    def test_creates_task_run_and_updates_last_run_at(self, task_book, task_run_book):
        _ = task_run_book
        task = _make_test_task(task_book, task_book._factory, "task_rt1")
        run = task_book.record_run_start(
        task_id=task.task_id,
            manual=False,
        )
        assert run is not None
        assert run.task_id == task.task_id
        assert run.manual is False
        assert run.status == "running"

        # Verify task.last_run_at was updated
        updated = task_book.get_by_task_id(task_id=task.task_id)
        assert updated is not None
        assert updated.last_run_at is not None

    def test_run_id_can_be_provided(self, task_book):
        task = _make_test_task(task_book, task_book._factory, "task_rt2")
        run = task_book.record_run_start(
            task_id=task.task_id,
            manual=True,
            run_id="my_run_42",
        )
        assert run.run_id == "my_run_42"


class TestMarkRunAtConsumed:
    def test_sets_enabled_to_zero(self, task_book, factory):
        # Use the contact id minted by the factory, not a hardcoded 42.
        uid = _seed_contact(factory)

        datetime.now(UTC).replace(tzinfo=None)
        future = datetime.now(UTC).replace(tzinfo=None, microsecond=0) + timedelta(hours=1)
        task = task_book.get(task_book.add(Task(name='One-shot Task', prompt='Run once', run_at=future, target_channel="webui", contact_id=uid, conversation_id=None, tz='UTC')))
        assert task.enabled == 1

        task_book.mark_run_at_consumed(task_id=task.task_id)
        updated = task_book.get_by_task_id(task_id=task.task_id)
        assert updated is not None
        assert updated.enabled == 0


class TestListAllEnabledForWorkers:
    def test_lists_enabled_user_tasks_across_uids(self, task_book, factory):
        from magi.old_bus.firmwares.books.local.contactBook import ContactBook

        # Two contacts so the test can assert both uids appear in
        # the worker-visible list.
        cbook = ContactBook(factory)
        cbook.get(cbook.add(Contact(name='contact-A', role=Role.ASSIGNED)))
        cbook.get(cbook.add(Contact(name='contact-B', role=Role.ASSIGNED)))
        contacts = cbook.list_all()
        uid_a, uid_b = contacts[0].id, contacts[1].id

        datetime.now(UTC).replace(tzinfo=None)
        task_book.get(task_book.add(Task(name='User A Task', prompt='do stuff', cron='0 9 * * *', target_channel="webui", contact_id=uid_a, conversation_id=None, tz='UTC')))
        task_book.get(task_book.add(Task(name='User B Task', prompt='do other stuff', cron='*/30 * * * *', target_channel="tg", contact_id=uid_b, conversation_id=None, tz='UTC')))

        tasks = task_book.list_all_enabled_for_workers()
        assert len(tasks) == 2
        uids = {t.contact_id for t in tasks}
        assert uid_a in uids
        assert uid_b in uids

    def test_excludes_disabled_tasks(self, task_book, factory):
        uid = _seed_contact(factory)

        datetime.now(UTC).replace(tzinfo=None)
        t = task_book.get(task_book.add(Task(name='Disabled Task', prompt='skip', cron='0 9 * * *', target_channel="webui", contact_id=uid, conversation_id=None, tz='UTC')))
        task_book.disable(task_id=t.task_id, contact_id=uid)

        tasks = task_book.list_all_enabled_for_workers()
        task_ids = {t.task_id for t in tasks}
        assert t.task_id not in task_ids


class TestReapStale:
    def test_flips_stuck_running_rows_to_failed(self, task_book, task_run_book):
        task = _make_test_task(task_book, task_book._factory, "task_stale")
        run = task_book.record_run_start(task_id=task.task_id, manual=False)

        # Simulate stale by backdating started_at
        stale_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=600)
        from sqlalchemy import select

        from magi.old_bus.firmwares.books.local.tasksBook import _TaskRunRow

        with task_run_book._session() as s:
            row = s.scalar(select(_TaskRunRow).where(_TaskRunRow.id == run.id))
            if row:
                row.started_at = stale_time
                s.commit()

        n = task_run_book.reap_stale(older_than_seconds=300)
        assert n == 1

        reaped = task_run_book.get_by_run_id(run_id=run.run_id)
        assert reaped is not None
        assert reaped.status == "failed"
        assert reaped.error == "abandoned by previous worker"

    def test_ignores_recent_running_rows(self, task_book, task_run_book):
        task = _make_test_task(task_book, task_book._factory, "task_recent")
        task_book.record_run_start(task_id=task.task_id, manual=False)

        n = task_run_book.reap_stale(older_than_seconds=300)
        assert n == 0  # should be too recent
