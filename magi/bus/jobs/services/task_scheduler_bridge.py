"""BUS-side bridge to task execution.

[plan amendment §11]: The old apscheduler-backed
``magi.channels.tasks.scheduler`` has been deleted; TaskWorker now
owns all scheduling. This bridge forwards ``request_manual_fire``
and ``fire_now_sync`` to new_bus ``runTaskJobBoard``.
``start`` / ``stop`` are no-ops (TaskWorker lifecycle is owned
by the composition root). ``notify_scheduled`` / ``notify_unscheduled``
are no-ops (TaskWorker rehydrates from DB on start and polls every
15s — the warm-cache push model is obsolete).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.bus.jobs.protocols.task import TaskFullView

if TYPE_CHECKING:
    from magi.bus.jobs.services.task import TaskService

logger = logging.getLogger("magi.bus.jobs.services.task_scheduler_bridge")


class TaskSchedulerBridge:
    """BUS-side façade over task execution (now via TaskWorker + new_bus).

    Construction is cheap (no DB or scheduler work).
    """

    def __init__(self) -> None:
        pass

    # -- warm-cache notifications (no-ops — TaskWorker polls DB) ---------

    def notify_scheduled(self, view: TaskFullView) -> None:
        """No-op: TaskWorker polls tasks_book every 15s."""
        pass

    def notify_unscheduled(self, task_id: str) -> None:
        """No-op: TaskWorker polls tasks_book every 15s."""
        pass

    # -- manual fire (via new_bus RunTaskJob) ----------------------------

    def request_manual_fire(self, task_id: str, *, run_id: str) -> None:
        """Publish a RunTaskJob to new_bus for TaskWorker to claim."""
        self._publish_run_task_job(task_id, run_id=run_id)

    async def fire_now_sync(self, task_id: str, *, run_id: str) -> None:
        """Publish a RunTaskJob to new_bus (same as request_manual_fire)."""
        self._publish_run_task_job(task_id, run_id=run_id)

    def fire_now_sync_threadsafe(
        self, task_id: str, *, run_id: str
    ) -> None:
        """Sync fallback: publish RunTaskJob through new_bus."""
        self._publish_run_task_job(task_id, run_id=run_id)

    # -- lifecycle (no-ops — TaskWorker is owned by composition root) ----

    def start(self) -> None:
        """No-op: TaskWorker started by composition root."""
        logger.debug("TaskSchedulerBridge.start: no-op (TaskWorker owns scheduling)")

    def stop(self) -> None:
        """No-op: TaskWorker stopped by composition root."""
        logger.debug("TaskSchedulerBridge.stop: no-op (TaskWorker owns scheduling)")

    # -- internal --------------------------------------------------------

    def _publish_run_task_job(self, task_id: str, *, run_id: str) -> None:
        """Publish a RunTaskJob through new_bus (if available)."""
        try:
            from magi.channels import get_current_new_bus
            bus = get_current_new_bus()
            if bus is None:
                logger.warning(
                    "TaskSchedulerBridge: new_bus not available; "
                    "task %s not fired", task_id,
                )
                return

            from magi.new_bus.guild.runTaskJob import RunTaskJob
            bus.run_task_job_board.publish(RunTaskJob(
                task_id=task_id,
                manual=True,
                fired_by="api_manual_run",
            ))
            logger.info(
                "TaskSchedulerBridge: published RunTaskJob for task %s", task_id,
            )
        except Exception:
            logger.exception(
                "TaskSchedulerBridge: failed to publish RunTaskJob for %s",
                task_id,
            )


__all__ = ["TaskSchedulerBridge"]