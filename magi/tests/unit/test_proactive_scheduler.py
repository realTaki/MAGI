"""Regression tests for :class:`magi.agent.proactive.scheduler.TaskScheduler`.

Catches the botched ``__init__`` edit where a ``@property``
declaration was pasted inside the constructor body,
silently dropping ``self._sched = BackgroundScheduler(...)``
and every subsequent attribute assignment. The class
silently constructed (just ``self._state_dir = state_dir``
ran) and only failed at the first call site — typically
``schedule_task`` from the chat path, with
``AttributeError: 'TaskScheduler' object has no attribute
'_sched'``.

The smoke here pins the cheapest invariant:

  1. ``__init__`` populates the four instance attributes
     the rest of the class touches (``_sched``, ``_loop``,
     ``_executor``, ``on_task_failure``).
  2. The ``start_scheduler`` factory still wires one up
     + starts it (basic lifecycle smoke).
  3. ``get_scheduler()`` returns the running singleton.

We don't exercise ``register`` / fire-on-cron here — those
live in their own broader integration smoke. The bug
was structural (attributes missing after init), not
behavioural (race / cron misfire).
"""

from __future__ import annotations

import tempfile
from typing import Iterator

import pytest

from magi.agent.proactive.scheduler import (
    TaskScheduler,
    _DEFAULT_EXECUTOR_WORKERS,
    _reset_for_tests,
    get_scheduler,
    start_scheduler,
    stop_scheduler,
)


@pytest.fixture
def fresh_state_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Per-test state directory + ``MAGI_STATE_DIR`` env var.

    The env var matters because ``start_scheduler`` →
    ``start()`` lazily initialises the SQLAlchemy engine,
    which calls :func:`magi.agent.db.engine.require_state_dir`
    and raises ``_MissingStateDirError`` when unset. The
    bare-init test (``test_init_populates_required_attributes``)
    doesn't trigger the engine — passes without — but
    starting the scheduler does, so we set it for the
    whole fixture for safety.

    The tmp dir isn't actively deleted; the scheduler's
    thread-pool teardown is what matters, and
    ``stop_scheduler`` in the teardown handles that.
    """
    sd = tempfile.mkdtemp(prefix="magi-scheduler-test-")
    monkeypatch.setenv("MAGI_STATE_DIR", sd)
    yield sd
    # Defensive: stop in case a test forgot. Idempotent.
    try:
        stop_scheduler(wait=False)
    except Exception:  # noqa: BLE001
        pass
    _reset_for_tests()


def test_init_populates_required_attributes(fresh_state_dir: str) -> None:
    """``__init__`` must populate every attribute the rest of
    the class references. Catches the botched-edit class of
    bugs where a ``@property`` heading lands inside
    ``__init__`` at the wrong indent and silently swallows
    the rest of the constructor body.
    """
    sch = TaskScheduler(fresh_state_dir)
    # These four are referenced from ``start()`` /
    # ``register()`` / ``unregister()`` / ``shutdown()``:
    assert hasattr(sch, "_sched"), (
        "TaskScheduler.__init__ did not initialise "
        "self._sched — class is structurally broken (the "
        "post-construction body was eaten by a misplaced "
        "@property definition)."
    )
    assert sch._sched is not None
    # Loop + thread are populated to None and lazily
    # replaced by ``start()``:
    assert hasattr(sch, "_loop")
    assert hasattr(sch, "_executor")
    assert sch._executor is not None
    # ``apscheduler.executors.pool.ThreadPoolExecutor``
    # doesn't expose ``max_workers`` back out — the value
    # has to live on the instance for the ``start()``
    # log line and any future assertions.
    assert sch._executor_max_workers == _DEFAULT_EXECUTOR_WORKERS
    assert hasattr(sch, "on_task_failure")
    # on_task_failure is intentionally None until failure.py
    # binds its log-only handler at boot.
    assert sch.on_task_failure is None
    # Public surface:
    assert sch.state_dir == fresh_state_dir


def test_start_scheduler_factory_idempotent(fresh_state_dir: str) -> None:
    """``start_scheduler(state_dir)`` builds one singleton,
    starts the loop thread + apscheduler, and returns the
    same instance on subsequent calls.

    The factory returned ``None`` here in the past when
    init silently failed (the botched-edit variant) — that
    failure mode doesn't even reach this assertion, but the
    structural-attribute check above is the cheaper guard.
    """
    sch = start_scheduler(fresh_state_dir)
    assert sch is not None
    assert isinstance(sch, TaskScheduler)
    second = start_scheduler(fresh_state_dir)
    # Idempotent: same instance on second call.
    assert second is sch


def test_get_scheduler_after_start(fresh_state_dir: str) -> None:
    """``get_scheduler()`` returns the running singleton."""
    started = start_scheduler(fresh_state_dir)
    fetched = get_scheduler()
    assert fetched is started
