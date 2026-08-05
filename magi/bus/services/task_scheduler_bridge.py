"""BUS-side bridge to the apscheduler-backed ``magi.channels.tasks.scheduler``.

This module is the **only** Python module under ``magi.bus`` that imports
``magi.channels.tasks.scheduler`` (or ``magi.channels.tasks.channel``).
Exposing the scheduler behind a BUS-side service keeps
``magi.channels.api`` — and every other domain package — from holding a
direct Python reference to the scheduler, which is required by
``docs/MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md`` §5.6 + §6.

Why a bridge instead of an event queue
--------------------------------------
The doc's intended flow is "API → BUS → Tasks Worker → BUS → AgentWorker".
A full event-queue refactor (publish a ``task.scheduled`` event, have a
worker consume it) would require a durable outbox and a worker loop.
Minimal-by-default keeps the scheduler as an in-process warm cache of
the BUS's task rows, but the *only* Python entry point is this bridge —
so the boundary test's ``channels.api ⊥ channels.tasks`` rule stays
enforceable.

Contract surface
----------------
The bridge exposes three notify calls + one sync fallback:

- :meth:`notify_scheduled` — a row was just created / updated; nudge the
  warm cache. Best-effort; swallows "scheduler not running" because the
  DB row is the source of truth and the scheduler rehydrates from DB on
  next start.
- :meth:`notify_unscheduled` — a row was deleted or disabled; remove
  it from the warm cache. Same best-effort contract.
- :meth:`request_manual_fire` — fire a task NOW via the scheduler
  thread pool. **Re-raises** ``RuntimeError`` when the scheduler isn't
  running, so the caller can fall back to the in-process sync path.
- :meth:`fire_now_sync` — the in-process sync fallback for dev/test
  mode where the scheduler isn't started. Wraps
  ``TaskChannel.dispatch``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.bus.protocols.task import TaskFullView, TaskScheduleView

if TYPE_CHECKING:
    from magi.bus.services.task import TaskService

logger = logging.getLogger("magi.bus.services.task_scheduler_bridge")


class TaskSchedulerBridge:
    """BUS-side façade over the apscheduler-backed task scheduler.

    Construction is cheap (no DB or scheduler work); the underlying
    scheduler singleton is touched lazily on the first notify / fire
    call. This keeps importing the bridge side-effect free and lets
    tests construct a bridge without booting apscheduler.
    """

    def __init__(self) -> None:
        pass

    # -- warm-cache notifications ---------------------------------------

    def notify_scheduled(self, view: TaskFullView) -> None:
        """Best-effort: register/update an enabled task in the scheduler.

        On the "scheduler not running" path the DB row is authoritative;
        the next scheduler start rehydrates from the BUS. We log + swallow
        so a missing scheduler never fails the user-visible request.
        """
        schedule_view = TaskScheduleView(
            id=view.id,
            enabled=view.enabled,
            cron=view.cron,
            run_at=view.run_at,
        )
        try:
            self._scheduler().register(schedule_view)
        except RuntimeError:
            logger.info(
                "scheduler not running yet; task %s will activate on next start",
                view.id,
            )
        except Exception as exc:  # noqa: BLE001 — boundary around a 3rd-party scheduler
            logger.warning(
                "scheduler.register(%s) failed (DB row is still authoritative): %s",
                view.id, exc,
            )

    def notify_unscheduled(self, task_id: str) -> None:
        """Best-effort: remove ``task_id`` from the scheduler's warm cache."""
        try:
            self._scheduler().unregister(task_id)
        except RuntimeError:
            # Scheduler not running — the row is already gone from the DB,
            # so there's nothing to remove from the cache. Silent OK.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler.unregister(%s) failed: %s", task_id, exc)

    # -- manual fire -----------------------------------------------------

    def request_manual_fire(self, task_id: str, *, run_id: str) -> None:
        """Ask the running scheduler to fire ``task_id`` immediately.

        Raises ``RuntimeError`` (propagated from the scheduler singleton)
        when the scheduler isn't running. Callers handle that with
        :meth:`fire_now_sync`.
        """
        self._scheduler().submit_now(task_id, run_id=run_id)

    async def fire_now_sync(self, task_id: str, *, run_id: str) -> None:
        """In-process sync fallback used when the scheduler isn't running.

        Reaches ``TaskChannel.dispatch`` directly so the API can honour
        ``POST /api/tasks/{id}/run`` even in dev / single-container /
        pytest mode where the apscheduler thread was never started.
        """
        from magi.channels.tasks.channel import TaskChannel

        await TaskChannel.dispatch(
            task_id,
            manual=True,
            pre_created_run_id=run_id,
        )

    # -- sync helper for tests / dev callers -----------------------------

    def fire_now_sync_threadsafe(
        self, task_id: str, *, run_id: str
    ) -> None:
        """Run :meth:`fire_now_sync` on a fresh asyncio loop.

        Convenience for sync API endpoints that need the same dev-mode
        fallback as the previous ``asyncio.run(TaskChannel.dispatch(...))``
        path. Production (scheduler-up) goes through
        :meth:`request_manual_fire` instead.
        """
        asyncio.run(self.fire_now_sync(task_id, run_id=run_id))

    # -- lifecycle (Phase 5: keep __main__.py from reaching scheduler) --

    def start(self) -> None:
        """Start the apscheduler-backed task worker.

        Phase 5 — keeps callers (``magi __main__.py`` and the
        runtime's :func:`worker_lifespan`) from importing
        ``magi.channels.tasks.scheduler`` directly.  This bridge
        remains the single Python seam between the BUS and the
        scheduler (per plan §5.5 / boundary-test rule).
        """
        from magi.channels.tasks.scheduler import start_scheduler

        start_scheduler()
        logger.info("task scheduler started via bridge")

    def stop(self) -> None:
        """Stop the task worker if it's running; idempotent."""
        from magi.channels.tasks.scheduler import stop_scheduler

        stop_scheduler()
        logger.info("task scheduler stopped via bridge")

    # -- internal --------------------------------------------------------

    def _scheduler(self):
        """Lazily import the scheduler singleton.

        Importing lazily keeps this module side-effect free: a process
        that boots ``TaskSchedulerBridge`` but never calls a notify / fire
        method never touches apscheduler. The ``magi.__main__`` composition
        root decides whether the scheduler is started; this bridge just
        forwards.
        """
        from magi.channels.tasks.scheduler import get_scheduler
        return get_scheduler()


__all__ = ["TaskSchedulerBridge"]