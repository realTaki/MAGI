"""TaskWorker coverage against the current JobBoard-only BUS surface."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime

from bus import Bus, ChatNotify, FireTaskJob, GetTaskJob, JobStatus, RunTaskNotify, Task
from bus.firmware.books.taskBook import TaskBook
from channels.tasks.worker import TaskWorker


def test_init_has_no_in_memory_schedule_cursor() -> None:
    worker = TaskWorker()

    assert worker.worker_name == "task"
    assert worker.worker_kind == "scheduler"
    assert not hasattr(worker, "_next_fire")


def test_should_fire_cron_coalesces_each_window() -> None:
    worker = TaskWorker()
    task = Task(id=1, name="hourly", prompt="do work", cron="0 * * * *")
    now = datetime.now(UTC)

    assert worker._should_fire(task, now) is True

    task = replace(task, updated_at=now.replace(tzinfo=None))

    assert worker._should_fire(task, now) is False


def test_fire_task_job_updates_the_task_timestamp(tmp_path) -> None:
    with Bus(tmp_path) as bus:
        task_book = TaskBook(bus._memories)
        task_id = task_book.add(Task(name="daily", prompt="summarise progress"))
        before_fire = task_book.get(task_id)
        assert before_fire is not None

        board = bus.board(FireTaskJob)
        assert board is not None
        result = board.get_result(board.publish(FireTaskJob(task_id=task_id)))

        assert result is not None
        assert result.status is JobStatus.COMPLETED
        after_fire = task_book.get(task_id)
        assert after_fire is not None
        assert after_fire.updated_at > before_fire.updated_at


def test_worker_claims_trigger_and_publishes_chat_notify(tmp_path) -> None:
    with Bus(tmp_path) as bus:
        # Setup is Firmware-internal; TaskWorker itself sees only JobBoards.
        task_book = TaskBook(bus._memories)
        task_id = task_book.add(
            Task(name="daily", prompt="summarise progress")
        )
        before_fire = task_book.get(task_id)
        assert before_fire is not None
        worker = TaskWorker(poll_seconds=0.01)
        assert worker.attach(bus)
        try:
            trigger_board = bus.board(RunTaskNotify)
            chat_board = bus.board(ChatNotify)
            assert trigger_board is not None
            assert chat_board is not None
            trigger_id = trigger_board.publish(RunTaskNotify(task_id=task_id))

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
            task_job_id = task_board.publish(GetTaskJob(task_id=task_id))
            fired_task = task_board.get_result(task_job_id)
            assert fired_task is not None
            assert fired_task.task is not None
            assert fired_task.task.updated_at > before_fire.updated_at

            chat = chat_board.claim()
            assert chat is not None
            assert chat.conversation_id is None
            assert "name: daily" in chat.text
            assert "summarise progress" in chat.text
        finally:
            worker.detach()
