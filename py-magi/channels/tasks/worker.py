"""TaskWorker — cron and RunTaskNotify both become ChatNotify."""

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

    def __init__(self, *, poll_seconds: float = 60.0) -> None:
        super().__init__(poll_seconds=poll_seconds)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                trigger = await self.call(self._board(RunTaskNotify).claim)
                if trigger is not None:
                    go(self._on_trigger(trigger))
                    continue
                for task in await self._due_tasks():
                    go(self._fire(task))
            except Exception:  # noqa: BLE001 -- a BUS blip must not kill the scheduler
                logger.exception("task worker: BUS operation failed")
            await asyncio.sleep(self.poll_seconds)

    def _board(self, job_type):
        assert self.bus is not None
        return self.bus.board(job_type)

    async def _due_tasks(self) -> list[Task]:
        board = self._board(ListTasksJob)
        job_id = await self.call(board.publish, ListTasksJob(enabled=True))
        listed = await board.get_result(job_id)
        if listed is None or listed.status is not JobStatus.COMPLETED:
            logger.warning(
                "task worker: %s",
                listed.error if listed is not None else "list tasks result is unavailable",
            )
            return []
        now = datetime.now(UTC)
        return [task for task in listed.tasks if self._should_fire(task, now)]

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

    async def _on_trigger(self, trigger: RunTaskNotify) -> None:
        try:
            board = self._board(GetTaskJob)
            job_id = await self.call(board.publish, GetTaskJob(task_id=trigger.task_id))
            got = await board.get_result(job_id)
            if got is None or got.status is not JobStatus.COMPLETED:
                error = (got.error if got is not None else None) or "get task failed"
            elif got.task is None:
                error = f"task {trigger.task_id} does not exist"
            elif not got.task.enabled:
                error = f"task {trigger.task_id} is disabled"
            else:
                error = await self._fire(got.task, manual=trigger.manual)
            await self._submit(trigger, error)
        except asyncio.CancelledError:
            await self._submit(trigger, "task worker cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- one task cannot kill the worker
            logger.exception("task worker: unhandled trigger %s", trigger.id)
            await self._submit(trigger, str(exc))

    async def _fire(self, task: Task, *, manual: bool = False) -> str | None:
        schedule = "manual" if manual else task.cron or "unscheduled"
        text = (
            "[task context]\nYou are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\nschedule: {schedule}\n\n[task prompt]\n{task.prompt}"
        )
        try:
            await self.call(
                self._board(ChatNotify).publish,
                ChatNotify(
                    publisher="task", conversation_id=task.conversation_id, text=text
                ),
            )
            board = self._board(FireTaskJob)
            job_id = await self.call(board.publish, FireTaskJob(task_id=task.id))
            stamped = await board.get_result(job_id)
            if stamped is None or stamped.status is not JobStatus.COMPLETED:
                error = (stamped.error if stamped is not None else None) or "record task fire failed"
                logger.warning("task worker: %s", error)
                return error
            return None
        except Exception as exc:  # noqa: BLE001 -- cron go() has no caller to report to
            logger.exception("task worker: could not fire task %s", task.id)
            return str(exc)

    async def _submit(self, trigger: RunTaskNotify, error: str | None) -> None:
        result = (
            RunTaskNotifyResult(id=trigger.id)
            if error is None
            else RunTaskNotifyResult(id=trigger.id, status=JobStatus.FAILED, error=error)
        )
        if not await self.call(self._board(RunTaskNotify).submit_result, result):
            logger.warning("task worker: failed to submit trigger result for %s", trigger.id)
