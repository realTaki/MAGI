"""TaskScheduler — owns the apscheduler instance + lifecycle.

## Why a dedicated event loop
apscheduler ships in two flavours:

- ``BlockingScheduler`` — runs on a synchronous main thread.
- ``BackgroundScheduler`` — wraps a real scheduler in a
  non-blocking daemon thread; jobs fire on a
  ``ThreadPoolExecutor``.

The project's FastAPI endpoint already runs an asyncio
loop on the main thread. We can't share it with the
scheduler:

1. Cron fires should not stall request handlers.
2. apscheduler's async integration (``AsyncIOScheduler``)
   would couple the two loops; shutdown semantics get
   fiddly.

We therefore run a *dedicated* asyncio loop in a worker
thread, owned by :class:`TaskScheduler`. apscheduler's
``BackgroundScheduler`` does the cron timing; the worker
thread does the agent-loop bridge via
``loop.call_soon_threadsafe`` / ``asyncio.run_coroutine_threadsafe``.
This keeps the cron timer + executor simple (the standard
sync ``BackgroundScheduler``) while making the runner a
plain async coroutine — testable in isolation against a
real event loop.

## Lifecycle

1. :func:`start_scheduler(state_dir)` — called once from
   :func:`magi.node.run` after ``init_orm``. Builds the
   singleton, starts the loop thread + scheduler, reads
   enabled tasks from the DB and re-registers each.
2. The module-level :func:`get_scheduler()` returns the
   singleton so the API router, the tool, and the failure
   hooks can call ``register``/``unregister`` without
   having to pass the instance through every call site.
3. :func:`stop_scheduler(wait=False)` — called at FastAPI
   shutdown. Stops the apscheduler instance and joins
   the loop thread.

## Why not a single asyncio.run_coroutine_threadsafe call?

``asyncio.run_coroutine_threadsafe(coro, loop).result()``
would block the apscheduler worker thread waiting for the
coroutine to complete — that ties up one slot in
apscheduler's default 1-worker ``ThreadPoolExecutor`` for
the entire duration of a fire. We surface that constraint
via :attr:`_sync_executor_max_workers` (default 4) so
several fires can run concurrently without starving the
trigger thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from magi.agent.proactive.orm_models import Task
from magi.agent.proactive.runner import execute_task
from magi.agent.db import open_session

logger = logging.getLogger("magi.agent.proactive.scheduler")


_DEFAULT_EXECUTOR_WORKERS = 4


class TaskScheduler:
    """Module-singleton proxy wrapping ``apscheduler.BackgroundScheduler``.

    Public surface used by the API / tool / failure hooks:
    :meth:`register`, :meth:`unregister`, :meth:`submit_now`,
    and the :attr:`on_task_failure` hook assignment.
    """

    def __init__(
        self,
        state_dir: str,
        *,
        executor_max_workers: int = _DEFAULT_EXECUTOR_WORKERS,
        coalesce: bool = True,
        misfire_grace_seconds: int = 300,
    ) -> None:
        self._state_dir = state_dir

    @property
    def state_dir(self) -> str:
        """Public read-only view of the bound state dir.

        Tools and tests occasionally need the path
        (e.g. to seed a profile fixture without going
        through the API). Returning a property keeps
        the field write-once invariant.
        """
        return self._state_dir
        self._coalesce = coalesce
        self._misfire_grace_seconds = misfire_grace_seconds

        # Daemon thread holds the asyncio loop that
        # the runner coroutines target. Started lazily
        # on first ``start()`` so constructing the
        # scheduler during boot (before the FastAPI
        # lifespan takes over) doesn't sit on a
        # half-built loop.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._loop_closed = False

        # The sync executor that bridges apscheduler
        # callback (sync) to the async runner. We
        # size it > 1 so several concurrent fires don't
        # stall the cron-trigger thread.
        self._executor = ThreadPoolExecutor(
            max_workers=executor_max_workers,
            thread_name_prefix="magi-task-fire",
        )

        self._sched = BackgroundScheduler(
            timezone="UTC",
            job_defaults={
                "coalesce": coalesce,
                "misfire_grace_time": misfire_grace_seconds,
                # ``replace_existing=True`` means register()
                # is idempotent on a re-insert (we re-call it
                # from the tool/API on every upsert).
                "replace_existing": True,
            },
        )
        self._sched.add_executor(self._executor)

        # Single attachment point for future failure-side
        # channels (email etc.). v0: failure.py binds this
        # to a log-only handler.
        self.on_task_failure: Optional[Callable[[str, str], None]] = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Begin scheduling.

        Idempotent: a second ``start()`` is a no-op
        (the BackgroundScheduler catches ``RuntimeError``
        internally and the loop-thread check returns
        early).
        """
        if self._loop_thread is not None:
            logger.debug("TaskScheduler.start called twice; ignoring")
            return
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="magi-task-loop",
            daemon=True,
        )
        self._loop_thread.start()
        # Block until the loop is live — ``register``
        # before this point would race against the loop's
        # startup (a fire's ``run_coroutine_threadsafe``
        # needs a running loop to schedule on).
        self._loop_ready.wait(timeout=5.0)
        self._sched.start()
        self._rehydrate_from_db()
        logger.info(
            "TaskScheduler started (state_dir=%s, executor_workers=%d)",
            self._state_dir, self._executor._max_workers,
        )

    def shutdown(self, *, wait: bool = True, cancel_running: bool = True) -> None:
        """Tear down the scheduler + the loop thread.

        Called from ``magi.node.run``'s shutdown path.
        ``wait=False`` is for tests — for prod we want
        to drain in-flight fires before the process exits.
        """
        try:
            self._sched.shutdown(wait=wait)
        except Exception:  # noqa: BLE001 — scheduler already shut down
            logger.debug("scheduler.shutdown raised; likely already down")
        if self._loop is not None and not self._loop.is_closed():
            if cancel_running:
                # Tell the loop to drop pending tasks; we
                # don't await long, just signal.
                self._loop.call_soon_threadsafe(self._loop.stop)
            else:
                # Drain: ask it to stop after current task finishes.
                self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5.0)
        self._executor.shutdown(wait=wait)
        self._loop = None
        self._loop_thread = None

    # -- registration -----------------------------------------------------

    def register(self, task: Task) -> None:
        """Add or update an enabled task. No-op for disabled tasks."""
        if not task.enabled:
            self.unregister(task.id)
            return
        # Pull the system tz freshly on every register.
        # We DO NOT cache at module-import — the operator
        # can change ``system.timezone`` from the Settings
        # tab and expect running tasks to follow.
        tz = self._resolve_tz()
        try:
            trigger = CronTrigger.from_crontab(task.cron, timezone=tz)
        except (ValueError, Exception) as exc:  # noqa: BLE001 — see plan
            logger.warning(
                "register: bad cron on task %s (%r): %s; skipping",
                task.id, task.cron, exc,
            )
            return
        self._sched.add_job(
            func=self._fire,
            trigger=trigger,
            id=task.id,
            args=[task.id, False],
            replace_existing=True,
        )

    def _resolve_tz(self) -> str:
        """Read the operator-configured system timezone.

        Falls back to ``UTC`` if the KV store is empty
        or the stored value can't be parsed as an IANA
        name. :func:`state_get` only reads — no exception
        surface here.
        """
        from magi.agent.db.settings import state_get

        raw = state_get(self._state_dir, "system.timezone") or "UTC"
        # ``state_get`` returns the raw string from the KV
        # store; the WebUI validator already rejected
        # garbage on save, so we accept whatever we got
        # and let apscheduler throw at construction
        # time. (``register`` falls through to "skipping"
        # logging when that happens.)
        return raw

    def unregister(self, task_id: str) -> None:
        """Remove a task from the scheduler. No-op if absent."""
        with contextlib.suppress(Exception):
            # apscheduler raises on unknown id; we treat
            # that as "already gone".
            self._sched.remove_job(task_id)

    def submit_now(self, task_id: str, *, run_id: str) -> None:
        """Fire the task immediately, bypassing cron.

        The caller (``POST /api/tasks/{id}/run``) pre-creates
        the ``TaskRun`` row and gives us its id so the
        scheduler thread doesn't race the API thread on
        inserts.
        """
        self._executor.submit(self._fire, task_id, True, run_id)

    # -- internal ---------------------------------------------------------

    def _fire(self, task_id: str, manual: bool, run_id: str | None = None) -> None:
        """Sync wrapper that bridges the executor → asyncio loop.

        Called on a worker thread for cron-driven fires
        or the manual ``submit_now``'s executor slot.
        Returns immediately after scheduling the
        coroutine; the runner writes the row's final
        state itself (success / failure / late-delete
        no-op).
        """
        if self._loop is None or self._loop.is_closed():
            logger.warning("_fire called after loop closed; dropping task %s", task_id)
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                execute_task(
                    self._state_dir,
                    task_id,
                    manual=manual,
                    pre_created_run_id=run_id,
                ),
                self._loop,
            )
            # Outer safety net — runner has its own
            # per-fire timeout (300s), so this future
            # is expected to resolve shortly after.
            try:
                fut.result(timeout=305)
            except Exception as exc:  # noqa: BLE001
                logger.exception("task %s fire raised past runner timeout", task_id)
                if self.on_task_failure is not None:
                    try:
                        self.on_task_failure(task_id, f"{type(exc).__name__}:{exc}")
                    except Exception:  # noqa: BLE001
                        pass
        except RuntimeError as exc:
            # ``run_coroutine_threadsafe`` raises if the
            # loop is closed mid-call. Drop the fire.
            logger.info("loop closed mid-fire for task %s: %s", task_id, exc)

    def _rehydrate_from_db(self) -> None:
        """On startup, read enabled tasks from the DB and re-register each.

        apscheduler's own jobstore is in-memory and only
        lives for one process — every boot needs to
        rebuild from the DB so cron schedules survive
        restarts.
        """
        with open_session() as db:
            tasks = (
                db.query(Task)
                .filter(Task.enabled == 1)
                .all()
            )
        for task in tasks:
            self.register(task)
        logger.info("rehydrated %d task(s) from DB", len(tasks))

    def _run_loop(self) -> None:
        """Daemonic target that owns the asyncio loop for runners."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop_closed = True
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass


# ──────────────────────────────────────────────────────────────────────── #
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────── #


_scheduler: Optional[TaskScheduler] = None
_scheduler_lock = threading.Lock()


def start_scheduler(state_dir: str) -> TaskScheduler:
    """Module singleton factory — idempotent.

    Subsequent calls return the existing scheduler. Tests
    that need a fresh scheduler use :func:`_reset_for_tests`.
    """
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = TaskScheduler(state_dir)
            _scheduler.start()
        return _scheduler


def get_scheduler() -> TaskScheduler:
    """Return the running singleton; ``RuntimeError`` if not started.

    Used by the API router, the tool, and the failure
    hook installer — they all assume the module has been
    wired up at boot.
    """
    if _scheduler is None:
        raise RuntimeError(
            "TaskScheduler is not running; "
            "call magi.agent.proactive.scheduler.start_scheduler() first"
        )
    return _scheduler


def stop_scheduler(*, wait: bool = True) -> None:
    """Idempotent stop. Safe to call from atexit or a finally."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=wait)
        finally:
            _scheduler = None


def _reset_for_tests() -> None:
    """Test-only: clear the singleton. Production never calls this."""
    global _scheduler
    _scheduler = None


__all__ = [
    "TaskScheduler",
    "start_scheduler",
    "stop_scheduler",
    "get_scheduler",
    "_reset_for_tests",
]
