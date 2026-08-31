"""TaskWorker — schedule task definitions and consume task-trigger Jobs.

The worker owns no Book or storage handle. It reads task DTOs and publishes
agent work only through the mounted BUS JobBoards.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from croniter import croniter as _croniter

from bus import (
    BaseWorker,
    ChatNotify,
    FireTaskJob,
    GetTaskJob,
    JobStatus,
    ListTasksJob,
    RunTaskNotify,
    RunTaskNotifyResult,
    Task,
    go,
)

logger = logging.getLogger("channels.tasks.worker")


class TaskWorker(BaseWorker):
    """Turn cron schedules and explicit triggers into ``ChatNotify`` Jobs."""

    worker_name = "task"
    worker_kind = "scheduler"

    def __init__(self, *, poll_seconds: float = 15.0) -> None:
        super().__init__(poll_seconds=poll_seconds)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                trigger = await self.call(self._board(RunTaskNotify).claim)
                if trigger is not None:
                    go(self._handle_trigger(trigger))
                    continue
                for task in await self._scheduled_tasks():
                    if self._should_fire(task, datetime.now(UTC)):
                        await self._handle_scheduled_task(task)
            except Exception:  # noqa: BLE001 -- a BUS blip must not kill the scheduler
                logger.exception("task worker: BUS operation failed")
            await asyncio.sleep(self.poll_seconds)

    def _board(self, job_type):
        assert self.bus is not None
        board = self.bus.board(job_type)
        return board

    async def _scheduled_tasks(self) -> list[Task]:
        result = await self._operate(ListTasksJob, ListTasksJob(enabled=True), "list tasks")
        return result.tasks

    def _should_fire(self, task: Task, now: datetime) -> bool:
        """Return whether *task* entered a new cron window since its last fire."""
        if not task.enabled or not task.cron:
            return False
        if now.tzinfo is not None:
            now = now.astimezone(UTC).replace(tzinfo=None)
        try:
            previous_window = _croniter(task.cron, now).get_prev(datetime)
        except (KeyError, ValueError):
            logger.warning("task worker: invalid cron for task %s: %r", task.id, task.cron)
            return False
        return task.updated_at is None or previous_window > task.updated_at

    async def _handle_trigger(self, trigger: RunTaskNotify) -> None:
        try:
            task = await self._task(trigger.task_id)
            if task is None:
                await self._fail(trigger, f"task {trigger.task_id} does not exist")
                return
            if not task.enabled:
                await self._fail(trigger, f"task {trigger.task_id} is disabled")
                return
            await self._fire_task(task, manual=trigger.manual)
            await self._complete(trigger)
        except asyncio.CancelledError:
            await self._fail(trigger, "task worker cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- one task cannot kill the worker
            logger.exception("task worker: unhandled trigger %s", trigger.id)
            await self._fail(trigger, str(exc))

    async def _handle_scheduled_task(self, task: Task) -> None:
        """Handle one due cron Task without blocking later scheduled Tasks."""
        try:
            await self._fire_task(task)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- one task cannot stop the scheduler
            logger.exception("task worker: could not fire scheduled task %s", task.id)

    async def _task(self, task_id: int) -> Task | None:
        result = await self._operate(GetTaskJob, GetTaskJob(task_id=task_id), "get task")
        return result.task

    async def _fire_task(self, task: Task, *, manual: bool = False) -> None:
        schedule = "manual" if manual else task.cron or "unscheduled"
        text = (
            "[task context]\nYou are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\nschedule: {schedule}\n\n[task prompt]\n{task.prompt}"
        )
        await self.call(
            self._board(ChatNotify).publish,
            ChatNotify(publisher="task", conversation_id=task.conversation_id, text=text),
        )
        await self._operate(FireTaskJob, FireTaskJob(task_id=task.id), "record task fire")

    async def _operate(self, job_type, job, operation: str):
        board = self._board(job_type)
        job_id = await self.call(board.publish, job)
        result = await self.call(board.get_result, job_id)
        if result is None:
            raise RuntimeError(f"{operation} result is unavailable")
        if result.status is not JobStatus.COMPLETED:
            raise RuntimeError(result.error or f"{operation} failed")
        return result

    async def _complete(self, trigger: RunTaskNotify) -> None:
        result = RunTaskNotifyResult(id=trigger.id)
        if not await self.call(self._board(RunTaskNotify).submit_result, result):
            logger.warning("task worker: failed to submit trigger result for %s", trigger.id)

    async def _fail(self, trigger: RunTaskNotify, error: str) -> None:
        result = RunTaskNotifyResult(
            id=trigger.id,
            status=JobStatus.FAILED,
            error=error,
        )
        if not await self.call(self._board(RunTaskNotify).submit_result, result):
            logger.warning("task worker: failed to submit failure for %s", trigger.id)
