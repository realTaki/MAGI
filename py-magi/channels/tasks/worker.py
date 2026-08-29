"""TaskWorker — cron poll + RunTaskJob claim 双输入。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from croniter import croniter as _croniter

from old_bus.bases.job import JobStatus
from runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from old_bus import Bus
    from old_bus.firmwares.jobs.runTaskJob import RunTaskJob

logger = logging.getLogger("channels.task.worker")


class TaskWorker(RuntimeWorker):
    worker_name = "task"
    worker_kind = "scheduler"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 15.0,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds, concurrency=concurrency)
        self._next_fire: dict[str, datetime] = {}
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._rehydrated = False

    async def register_channel(self) -> None:
        """Advertise the internal task trigger during worker startup."""
        await self.call(self.bus.settings_book.register_channel, name="task")

    async def on_start(self) -> bool | None:
        await self.register_channel()
        return None

    async def _run(self) -> None:
        await self._rehydrate()
        await self._reap_stale_runs()
        self._rehydrated = True
        while not self._stopping:
            await self.reserve_capacity()
            try:
                rj = await asyncio.to_thread(
                    self.bus.run_task_job_board.claim,
                    worker_id=self.worker_id,
                )
            except Exception:
                self.release_capacity()
                rj = None
            else:
                if rj is not None:
                    self.spawn_reserved(
                        self._handle_run_task_job_serialized(rj),
                        name=f"run-task-{rj.job_id}",
                    )
                    continue
                self.release_capacity()
            try:
                tasks = await self.call(self.bus.tasks_book.list_all_enabled_for_workers)
            except Exception:
                tasks = []
            now = datetime.now(UTC).replace(tzinfo=None)
            for task in tasks:
                if self._stopping:
                    break
                if self._should_fire(task, now):
                    try:
                        await self._fire_task(task, manual=False)
                        if task.run_at and not task.cron:
                            await self.call(
                                self.bus.tasks_book.mark_run_at_consumed, task_id=task.task_id
                            )
                    except Exception:
                        logger.exception("TaskWorker: _fire_task failed for %s", task.task_id)
            self._last_poll_at = now
            await asyncio.sleep(self.poll_seconds)

    def _should_fire(self, task, now: datetime) -> bool:
        if now.tzinfo is not None:
            now = now.astimezone(UTC).replace(tzinfo=None)
        if not getattr(task, "enabled", 1):
            return False
        if task.run_at and not task.cron:
            return task.run_at <= now and self._next_fire.get(task.task_id) is None
        if task.cron:
            return self._should_fire_cron(task, now)
        return False

    def _should_fire_cron(self, task, now: datetime) -> bool:
        if not task.cron:
            return False
        try:
            cron_iter = _croniter(task.cron, now)
            prev_fire = cron_iter.get_prev(datetime)
        except (ValueError, KeyError):
            return False
        last = self._next_fire.get(task.task_id)
        return last is None or (prev_fire and prev_fire > last)

    async def _fire_task(self, task, *, manual: bool = False) -> None:
        """Fire a single task — assumes ``task`` is fully populated.

        ``conversation_id`` / ``contact_id`` come from the Task row
        itself (set at task creation via
        :meth:`conversations_book.create_task_conversation` and
        :attr:`Task.contact_id`); they are NOT overridable per-run,
        so every run accumulates into the same conversation and the
        caller can't accidentally strand a run in the wrong session.
        """
        task_id = task.task_id
        # Contract guard: a task without ``conversation_id`` means
        # the create-path skipped ``create_task_conversation`` (or a
        # legacy row slipped through). Refuse to fire rather than
        # publish a chatNotifyJob that ``build_messages_from_conversation``
        # can't resolve.
        if not task.conversation_id:
            raise ValueError(
                f"task {task_id!r} has no conversation_id; "
                f"task creation must call create_task_conversation"
            )
        schedule_desc = (
            task.cron if task.cron else (f"once at {task.run_at}" if task.run_at else "ad-hoc")
        )
        contextual_prompt = (
            f"[task context]\nYou are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\nschedule: {schedule_desc}\n"
            f"channel: {getattr(task, 'target_channel', 'webui')}\n\n[task prompt]\n{task.prompt}"
        )
        try:
            await self.call(self.bus.tasks_book.record_run_start, task_id=task_id, manual=manual)
        except Exception:
            pass
        # The user message is persisted to ``chat_messages`` inside
        # :meth:`chatNotifyBoard.publish`. No direct write here.
        from old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob

        await self.call(
            self.bus.agent_job_board.publish,
            ChatNotifyJob(
                text=contextual_prompt,
                channel="task",
                contact_id=task.contact_id,
                conversation_id=task.conversation_id,
            ),
        )
        self._next_fire[task_id] = datetime.now(UTC).replace(tzinfo=None)

    async def _handle_run_task_job(self, rj: RunTaskJob) -> None:
        from old_bus.firmwares.jobs.runTaskJob import RunTaskResult

        try:
            task = await self.call(self.bus.tasks_book.get, task_id=rj.task_id)
            if task is None:
                await self.call(
                    self.bus.run_task_job_board.submit_result,
                    job_id=rj.job_id,
                    worker_id=self.worker_id,
                    result=RunTaskResult(job_id=rj.job_id, status=JobStatus.FAILED, error="task not found"),
                )
                return
            # ``_fire_task`` raises if ``task.conversation_id`` is
            # missing; the surrounding ``except`` flips the job to
            # FAILED with that error. Keeps the create-task contract
            # loud instead of producing a chatNotifyJob with no session.
            await self._fire_task(task, manual=rj.manual)
            await self.call(
                self.bus.run_task_job_board.submit_result,
                job_id=rj.job_id,
                worker_id=self.worker_id,
                result=RunTaskResult(job_id=rj.job_id, status=JobStatus.COMPLETED),
            )
        except Exception as exc:
            await self.call(
                self.bus.run_task_job_board.submit_result,
                job_id=rj.job_id,
                worker_id=self.worker_id,
                result=RunTaskResult(job_id=rj.job_id, status=JobStatus.FAILED, error=str(exc)[:1024]),
            )

    async def _handle_run_task_job_serialized(self, rj: RunTaskJob) -> None:
        """Run unrelated task ids concurrently, never fire one task twice."""
        lock = self._task_locks.setdefault(rj.task_id, asyncio.Lock())
        async with lock:
            await self._handle_run_task_job(rj)

    async def _rehydrate(self) -> None:
        try:
            tasks = await self.call(self.bus.tasks_book.list_all_enabled_for_workers)
        except Exception:
            tasks = []
        # ``_next_fire`` is typed ``dict[str, datetime]`` (invariant value
        # type — see :class:`RuntimeWorker`). Tasks that have never fired
        # (``last_run_at IS NULL``) carry no useful entry: a missing key
        # and a ``None`` value both read as ``None`` via ``.get(task.task_id)``,
        # so dropping them keeps the runtime invariant without losing
        # information.
        #
        # ``last_run_at`` is already a ``datetime`` from the Book;
        # ``is not None`` is explicit because midnight ``00:00:00``
        # datetimes are falsy and would otherwise be silently dropped.
        self._next_fire = {
            t.task_id: t.last_run_at for t in tasks if t.last_run_at is not None
        }

    async def _reap_stale_runs(self) -> None:
        try:
            n = await self.call(self.bus.task_runs_book.reap_stale, older_than_seconds=300)
            if n:
                logger.info("TaskWorker: reaped %d stale task run(s)", n)
        except Exception:
            pass
