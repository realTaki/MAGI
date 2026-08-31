"""TaskWorker — cron and RunTaskNotify both become ChatNotify."""

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

    def __init__(self, *, poll_seconds: float = 60.0) -> None:
        super().__init__(poll_seconds=poll_seconds)

    async def _poll(self) -> bool:
        trigger = await self.claim(RunTaskNotify)
        if trigger is not None:
            go(self._on_trigger(trigger))
            return True
        for task in await self._due_tasks():
            self.publish(RunTaskNotify(task_id=task.id, manual=False))
        return False

    async def _due_tasks(self) -> list[Task]:
        try:
            listed = await self.ask(ListTasksJob(enabled=True))
        except Exception as exc:  # noqa: BLE001 -- a list failure skips this tick
            logger.warning("task worker: %s", exc)
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
            got = await self.ask(GetTaskJob(task_id=trigger.task_id))
            self._fire(got.task, manual=trigger.manual)
            self._submit(trigger, None)
        except asyncio.CancelledError:
            self._submit(trigger, "task worker cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- one task cannot kill the worker
            logger.exception("task worker: unhandled trigger %s", trigger.id)
            self._submit(trigger, str(exc))

    def _fire(self, task: Task, *, manual: bool = False) -> None:
        schedule = "manual" if manual else task.cron or "unscheduled"
        text = (
            "[task context]\nYou are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\nschedule: {schedule}\n\n[task prompt]\n{task.prompt}"
        )
        self.publish(
            ChatNotify(
                publisher="task", conversation_id=task.conversation_id, text=text
            )
        )

    def _submit(self, trigger: RunTaskNotify, error: str | None) -> None:
        result = (
            RunTaskNotifyResult(id=trigger.id)
            if error is None
            else RunTaskNotifyResult(id=trigger.id, status=JobStatus.FAILED, error=error)
        )
        self.submit(RunTaskNotify, result)
