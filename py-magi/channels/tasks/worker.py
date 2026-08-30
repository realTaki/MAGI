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
        self._next_fire: dict[int, datetime] = {}

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                trigger = await self.call(self._board(RunTaskNotify).claim)
                if trigger is not None:
                    go(self._handle_trigger(trigger))
                    continue
                for task in await self._scheduled_tasks():
                    if self._should_fire(task, datetime.now(UTC)):
                        await self._fire_task(task)
            except Exception:  # noqa: BLE001 -- a BUS blip must not kill the scheduler
                logger.exception("task worker: BUS operation failed")
            await asyncio.sleep(self.poll_seconds)

    def _board(self, job_type):
        assert self.bus is not None
        board = self.bus.board(job_type)
        assert board is not None, f"task worker: no JobBoard mounted for {job_type.__name__}"
        return board

    async def _scheduled_tasks(self) -> list[Task]:
        board = self._board(ListTasksJob)
        job_id = await self.call(board.publish, ListTasksJob(enabled=True))
        result = await self.call(board.get_result, job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            logger.warning(
                "task worker: could not list scheduled tasks (%s)",
                None if result is None else result.error,
            )
            return []
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
        previous_fire = self._next_fire.get(task.id)
        return previous_fire is None or previous_window > previous_fire

    async def _handle_trigger(self, trigger: RunTaskNotify) -> None:
        try:
            task = await self._task(trigger.task_id)
            if task is None:
                await self._submit(trigger, error=f"task {trigger.task_id} does not exist")
                return
            if not task.enabled:
                await self._submit(trigger, error=f"task {trigger.task_id} is disabled")
                return
            await self._fire_task(task, manual=trigger.manual)
            await self._submit(trigger)
        except asyncio.CancelledError:
            await self._submit(trigger, error="task worker cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- one task cannot kill the worker
            logger.exception("task worker: unhandled trigger %s", trigger.id)
            await self._submit(trigger, error=str(exc))

    async def _task(self, task_id: int) -> Task | None:
        board = self._board(GetTaskJob)
        job_id = await self.call(board.publish, GetTaskJob(task_id=task_id))
        result = await self.call(board.get_result, job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            raise RuntimeError(None if result is None else result.error or "task lookup failed")
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
        self._next_fire[task.id] = datetime.now(UTC).replace(tzinfo=None)

    async def _submit(self, trigger: RunTaskNotify, *, error: str | None = None) -> None:
        result = RunTaskNotifyResult(
            id=trigger.id,
            status=JobStatus.FAILED if error else JobStatus.COMPLETED,
            error=error,
        )
        if not await self.call(self._board(RunTaskNotify).submit_result, result):
            logger.warning("task worker: failed to submit trigger result for %s", trigger.id)
