"""TaskWorker — cron poll + RunTaskJob claim 双输入。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from croniter import croniter as _croniter
from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.runTaskJob import RunTaskJob

logger = logging.getLogger("magi.channels.task.worker")


class TaskWorker(ChannelWorker):
    channel_name = "task"

    def __init__(self, bus: Bus, *, poll_seconds: float = 15.0) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._next_fire: dict[str, datetime] = {}
        self._rehydrated = False

    async def _run(self) -> None:
        self._rehydrate(); self._reap_stale_runs(); self._rehydrated = True
        while not self._stopping:
            try: rj = await asyncio.to_thread(self.bus.run_task_job_board.claim)
            except Exception: rj = None
            if rj is not None:
                await self._handle_run_task_job(rj); continue
            try: tasks = self.bus.tasks_book.list_all_enabled_for_workers()
            except Exception: tasks = []
            now = datetime.now(timezone.utc)
            for task in tasks:
                if self._stopping: break
                if self._should_fire(task, now):
                    try:
                        await self._fire_task(task, fired_by="cron_tick")
                        if task.run_at and not task.cron:
                            self.bus.tasks_book.mark_run_at_consumed(task_id=task.id)
                    except Exception:
                        logger.exception("TaskWorker: _fire_task failed for %s", task.id)
            self._last_poll_at = now
            await asyncio.sleep(self.poll_seconds)

    def _should_fire(self, task, now: datetime) -> bool:
        if not getattr(task, "enabled", 1): return False
        if task.run_at and not task.cron:
            run_at = self._parse_datetime(task.run_at)
            return run_at is not None and run_at <= now and self._next_fire.get(task.id) is None
        if task.cron: return self._should_fire_cron(task, now)
        return False

    def _should_fire_cron(self, task, now: datetime) -> bool:
        if not task.cron: return False
        try:
            cron_iter = _croniter(task.cron, now); prev_fire = cron_iter.get_prev(datetime)
        except (ValueError, KeyError): return False
        last = self._next_fire.get(task.id)
        return last is None or (prev_fire and prev_fire > last)

    async def _fire_task(self, task, *, fired_by: str = "cron_tick", session_id: str | None = None, uid: int | None = None) -> None:
        from magi.bus.guild.chatJob import publish_chat
        task_id = task.id; effective_uid = uid or task.uid; effective_session = session_id or task.session_id
        schedule_desc = task.cron if task.cron else (f"once at {task.run_at}" if task.run_at else "ad-hoc")
        contextual_prompt = (
            f"[task context]\nYou are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\nschedule: {schedule_desc}\n"
            f"channel: {getattr(task, 'target_channel', 'webui')}\n\n[task prompt]\n{task.prompt}"
        )
        try: self.bus.tasks_book.record_run_start(task_id=task_id, trigger=fired_by)
        except Exception: pass
        if effective_session and effective_uid:
            try:
                self.bus.messages_book.add(session_id=effective_session,
                    role="user", text=contextual_prompt)
            except Exception: pass
        publish_chat(
            self.bus, text=contextual_prompt, channel="task",
            uid=effective_uid, session_id=effective_session,
            kind="task.triggered", task_id=task_id, fired_by=fired_by,
        )
        self._next_fire[task_id] = datetime.now(timezone.utc)

    async def _handle_run_task_job(self, rj: RunTaskJob) -> None:
        from magi.bus.guild.runTaskJob import RunTaskResult
        try:
            task = self.bus.tasks_book.get(task_id=rj.task_id)
            if task is None:
                self.bus.run_task_job_board.submit_result(key=rj.job_id, result=RunTaskResult(rj.job_id, False, error="task not found")); return
            await self._fire_task(task, fired_by=rj.fired_by, session_id=rj.session_id or task.session_id, uid=rj.uid or task.uid)
            self.bus.run_task_job_board.submit_result(key=rj.job_id, result=RunTaskResult(rj.job_id, True))
        except Exception as exc:
            self.bus.run_task_job_board.submit_result(key=rj.job_id, result=RunTaskResult(rj.job_id, False, error=str(exc)[:1024]))

    def _rehydrate(self) -> None:
        try: tasks = self.bus.tasks_book.list_all_enabled_for_workers()
        except Exception: tasks = []
        self._next_fire = {t.id: datetime.fromisoformat(t.last_run_at) if t.last_run_at else None for t in tasks}

    def _reap_stale_runs(self) -> None:
        try:
            n = self.bus.task_runs_book.reap_stale(older_than_seconds=300)
            if n: logger.info("TaskWorker: reaped %d stale task run(s)", n)
        except Exception: pass

    @staticmethod
    def _parse_datetime(s: str) -> datetime | None:
        try: return datetime.fromisoformat(s)
        except (ValueError, TypeError): return None
