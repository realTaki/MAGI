"""TaskWorker coverage against the current JobBoard-only BUS surface."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from bus import Bus, ChatNotify, JobStatus, RunTaskNotify, Task
from bus.firmware.books.taskBook import TaskBook
from channels.tasks.worker import TaskWorker


def test_init_keeps_only_scheduler_state() -> None:
    worker = TaskWorker()

    assert worker.worker_name == "task"
    assert worker.worker_kind == "scheduler"
    assert isinstance(worker._next_fire, dict)


def test_should_fire_cron_coalesces_each_window() -> None:
    worker = TaskWorker()
    task = Task(id=1, name="hourly", prompt="do work", cron="0 * * * *")
    now = datetime.now(UTC)

    assert worker._should_fire(task, now) is True

    worker._next_fire[task.id] = now.replace(tzinfo=None)

    assert worker._should_fire(task, now) is False


def test_worker_claims_trigger_and_publishes_chat_notify(tmp_path) -> None:
    with Bus(tmp_path) as bus:
        # Setup is Firmware-internal; TaskWorker itself sees only JobBoards.
        task_book = TaskBook(bus._memories)
        task_id = task_book.add(
            Task(name="daily", prompt="summarise progress", cron="0 9 * * *")
        )
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

            chat = chat_board.claim()
            assert chat is not None
            assert chat.conversation_id is None
            assert "name: daily" in chat.text
            assert "summarise progress" in chat.text
        finally:
            worker.detach()
