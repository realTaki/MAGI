"""TaskWorker coverage against the current JobBoard-only BUS surface."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime

from bus import (
    Bus,
    ChatNotify,
    GetConversationForChannelJob,
    GetTaskJob,
    JobStatus,
    RunTaskNotify,
    Task,
)
from bus.firmware.books.conversationBook import ConversationBook
from bus.firmware.books.taskBook import TaskBook
from channels.tasks.worker import TaskWorker


def _conversation(bus: Bus) -> int:
    return ConversationBook(bus._memories).add_for_channel(
        channel="test",
        delivery_address="local",
    )


def test_init_has_no_in_memory_schedule_cursor() -> None:
    assert TaskWorker.worker_name == "task"
    assert TaskWorker.worker_kind == "scheduler"
    assert "_next_fire" not in TaskWorker.__dict__


def test_should_fire_cron_coalesces_each_window(tmp_path) -> None:
    with Bus("@task-cron", workspace=tmp_path) as bus:
        worker = TaskWorker(bus)
        task = Task(id=1, conversation_id=1, name="hourly", prompt="do work", cron="0 * * * *")
        now = datetime.now(UTC)
        task = replace(task, updated_at=None)

        assert worker._should_fire(task, now) is True

        task = replace(task, updated_at=now.replace(tzinfo=None))

        assert worker._should_fire(task, now) is False


def test_run_task_notify_publish_updates_the_task_timestamp(tmp_path) -> None:
    with Bus("@task-prepare", workspace=tmp_path) as bus:
        task_book = TaskBook(bus._memories)
        task_id = task_book.add(
            Task(
                conversation_id=_conversation(bus),
                name="daily",
                prompt="summarise progress",
                cron="0 9 * * *",
            )
        )
        before_fire = task_book.get(task_id)
        assert before_fire is not None

        board = bus.board(RunTaskNotify)
        assert board is not None
        board.publish(RunTaskNotify(publisher="test", task_id=task_id))

        after_fire = task_book.get(task_id)
        assert after_fire is not None
        assert after_fire.updated_at > before_fire.updated_at


def test_worker_claims_trigger_and_publishes_chat_notify(tmp_path) -> None:
    with Bus("@task-claim", workspace=tmp_path) as bus:
        conversation_id = _conversation(bus)
        task_book = TaskBook(bus._memories)
        task_id = task_book.add(
            Task(
                conversation_id=conversation_id,
                name="daily",
                prompt="summarise progress",
                cron="0 9 * * *",
            )
        )
        before_fire = task_book.get(task_id)
        assert before_fire is not None
        worker = TaskWorker(bus, poll_seconds=0.01)
        assert worker.attach()
        try:
            trigger_board = bus.board(RunTaskNotify)
            chat_board = bus.board(ChatNotify)
            assert trigger_board is not None
            assert chat_board is not None
            trigger_id = trigger_board.publish(
                RunTaskNotify(publisher="test", task_id=task_id)
            )

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if trigger_board.check_job_status(trigger_id) is JobStatus.COMPLETED:
                    break
                time.sleep(0.01)
            assert trigger_board.check_job_status(trigger_id) is JobStatus.COMPLETED

            trigger_result = trigger_board.get_result(trigger_id)
            assert trigger_result is not None
            assert trigger_result.status is JobStatus.COMPLETED

            task_board = bus.board(GetTaskJob)
            assert task_board is not None
            fired_task = task_board.publish(
                GetTaskJob(publisher="test", task_id=task_id)
            )
            assert fired_task.task is not None
            assert fired_task.task.updated_at > before_fire.updated_at

            chat = None
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                chat = chat_board.claim()
                if chat is not None:
                    break
                time.sleep(0.01)
            assert chat is not None
            assert chat.channel == "test"
            assert chat.delivery_address == "local"
            got = bus.board(GetConversationForChannelJob).publish(
                GetConversationForChannelJob(
                    publisher="test",
                    channel=chat.channel,
                    delivery_address=chat.delivery_address,
                )
            )
            assert got.conversation is not None
            assert got.conversation.id == conversation_id
            assert "name: daily" in chat.text
            assert "summarise progress" in chat.text
        finally:
            worker.detach()


def test_worker_marks_unknown_task_trigger_failed(tmp_path) -> None:
    with Bus("@task-missing", workspace=tmp_path) as bus:
        worker = TaskWorker(bus, poll_seconds=0.01)
        assert worker.attach()
        try:
            trigger_board = bus.board(RunTaskNotify)
            assert trigger_board is not None
            trigger_id = trigger_board.publish(
                RunTaskNotify(publisher="test", task_id=999)
            )

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if trigger_board.check_job_status(trigger_id) is JobStatus.FAILED:
                    break
                time.sleep(0.01)
            assert trigger_board.check_job_status(trigger_id) is JobStatus.FAILED

            result = trigger_board.get_result(trigger_id)
            assert result is not None
            assert result.status is JobStatus.FAILED
            assert result.error == "task 999 does not exist"
        finally:
            worker.detach()
