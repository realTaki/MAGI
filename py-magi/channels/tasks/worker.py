"""TaskWorker — cron and RunTaskNotify both become ChatNotify."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from croniter import croniter as _croniter

from bus import (
    BaseWorker,
    Bus,
    ChatNotify,
    GetTaskJob,
    JobStatus,
    ListTasksJob,
    RunTaskNotify,
    RunTaskNotifyResult,
    Task,
    go,
)


class TaskWorker(BaseWorker):
    """Turn cron schedules and explicit triggers into ``ChatNotify`` Jobs."""

    worker_name = "task"
    worker_kind = "scheduler"

    def __init__(self, bus: Bus, *, poll_seconds: float = 60.0) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._reported: set[int] = set()

    async def _poll(self) -> bool:
        trigger = await self.claim(RunTaskNotify)
        if trigger is not None:
            go(self._on_trigger(trigger))
            return True
        for task in await self._due_tasks():
            self.publish_notify(RunTaskNotify(task_id=task.id, manual=False, publisher=cast(str, self.worker_name)))
        return False

    async def _due_tasks(self) -> list[Task]:
        listed = await self.ask(ListTasksJob(enabled=True, publisher=cast(str, self.worker_name)))
        if listed is None:
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
            if task.id not in self._reported:
                self._reported.add(task.id)
                self._report(task, f"invalid cron: {task.cron!r}")
            return False
        return task.updated_at is None or previous_window > task.updated_at

    async def _on_trigger(self, trigger: RunTaskNotify) -> None:
        got = await self.ask(
            GetTaskJob(task_id=trigger.task_id, publisher=cast(str, self.worker_name))
        )
        if got is None or got.task is None:
            self._submit(trigger, f"task {trigger.task_id} does not exist")
            return
        self._fire(got.task, manual=trigger.manual)
        self._submit(trigger, None)

    def _report(self, task: Task, error: str) -> None:
        self.publish_notify(
            ChatNotify(
                publisher=cast(str, self.worker_name),
                conversation_id=task.conversation_id,
                text=f"[task error]\nname: {task.name}\n{error}",
            )
        )

    def _fire(self, task: Task, *, manual: bool = False) -> None:
        schedule = "manual" if manual else task.cron
        text = (
            "[task context]\nYou are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\nschedule: {schedule}\n\n[task prompt]\n{task.prompt}"
        )
        self.publish_notify(
            ChatNotify(
                publisher=cast(str, self.worker_name),
                conversation_id=task.conversation_id,
                text=text,
            )
        )

    def _submit(self, trigger: RunTaskNotify, error: str | None) -> None:
        result = (
            RunTaskNotifyResult(id=trigger.id)
            if error is None
            else RunTaskNotifyResult(id=trigger.id, status=JobStatus.FAILED, error=error)
        )
        self.submit(RunTaskNotify, result)
