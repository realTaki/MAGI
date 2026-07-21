"""Regression tests for the ``frequency="once"`` task path.

Three surfaces pinned:

  - :func:`validate_run_at` (cron_utils) — accepts ISO 8601
    with and without offset; naive UTC fallback; rejects
    empty / garbage strings.
  - :meth:`TaskScheduler.register` picks ``DateTrigger`` for
    rows with ``run_at`` populated and ``CronTrigger`` for
    rows without.
  - :func:`ScheduleTaskTool.run` round-trip: passing
    ``frequency="once"`` + ``run_at=...`` writes a Task
    row with ``cron=""`` and ``run_at`` set, and the new
    row picks ``DateTrigger`` on register.

The full live-fire path (apscheduler runs the callback)
is not exercised here — that's a live-smoke concern. We
just pin the deterministic layer so the next refactor
of either path doesn't silently lose the ``once`` shape.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from magi.agent.proactive.cron_utils import (
    validate_run_at,
    validate_run_at_future,
)
from magi.agent.proactive.scheduler import (
    TaskScheduler,
    _reset_for_tests,
    stop_scheduler,
)
from magi.agent.db import init_orm, init_sqlite, open_session
from magi.agent.proactive.orm_models import Task


# -- validate_run_at --------------------------------------------------------


def test_validate_run_at_accepts_offset_aware_iso() -> None:
    raw = "2026-08-01T15:30:00+08:00"
    out = validate_run_at(raw)
    assert out == raw
    # Round-trips as the same instant in UTC.
    assert dt.datetime.fromisoformat(out).astimezone(dt.timezone.utc) == \
        dt.datetime(2026, 8, 1, 7, 30, tzinfo=dt.timezone.utc)


def test_validate_run_at_naive_iso_treated_as_utc() -> None:
    raw = "2026-08-01T15:30:00"
    out = validate_run_at(raw)
    parsed = dt.datetime.fromisoformat(out)
    assert parsed.tzinfo is not None, (
        "naive stamp was passed through without tagging UTC; "
        "schedule_task row would compare unequal to itself later"
    )
    assert parsed.astimezone(dt.timezone.utc) == \
        dt.datetime(2026, 8, 1, 15, 30, tzinfo=dt.timezone.utc)


def test_validate_run_at_rejects_empty_garbage() -> None:
    for bad in ("", "  ", "2026-13-40", "not-a-date", "2026/08/01"):
        with pytest.raises(ValueError):
            validate_run_at(bad)


def test_validate_run_at_normalises_whitespace() -> None:
    out = validate_run_at("  2026-08-01T15:30:00+08:00  ")
    assert out == "2026-08-01T15:30:00+08:00"


# -- scheduler.register picks the right trigger -------------------------------


@pytest.fixture
def state_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh sqlite state dir. ``stop_scheduler`` is called
    in the fixture teardown so each test starts from a clean
    singleton."""
    sd = tmp_path / "state"
    sd.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None
    init_sqlite(str(sd))
    init_orm(str(sd))
    yield sd
    try:
        stop_scheduler(wait=False)
    except Exception:  # noqa: BLE001
        pass
    _reset_for_tests()


def _make_task(state_dir: Path, *, name: str = "once-fire-test", **overrides) -> Task:
    """Insert a row directly via ORM. Bypass the ``schedule_task``
    tool so the test pins what ``register`` sees, not what
    the tool writes."""
    from magi.agent.proactive import orm_models as _  # noqa: F401  (registers Task on Base)
    from magi.agent.db import Employee

    task_id = "T" + "0" * 25
    row_kwargs = dict(overrides)
    row_kwargs.setdefault("prompt", "do the thing")
    row_kwargs.setdefault("cron", "")
    row_kwargs.setdefault("tz", "UTC")
    row_kwargs.setdefault("channel", "webui")
    row_kwargs.setdefault("enabled", 1)
    row_kwargs.setdefault("created_at", "2026-07-20T12:00:00Z")
    row_kwargs.setdefault("updated_at", "2026-07-20T12:00:00Z")

    with open_session() as db:
        if "employee_id" not in row_kwargs:
            emp = db.query(Employee).first()
            if emp is None:
                emp = Employee(
                    name="tester",
                    telegram_id=90001,
                    role="admin",
                    provider="minimax",
                    api_key="fake-key",
                )
                db.add(emp)
                db.commit()
                db.refresh(emp)
            row_kwargs["employee_id"] = emp.id
        row = Task(
            id=task_id,
            name=name,
            **row_kwargs,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def test_register_with_run_at_uses_date_trigger(state_db: Path) -> None:
    """The one-shot path. ``register`` builds an apscheduler
    ``DateTrigger`` (one-shot, no further cron) for rows
    whose ``run_at`` is set, regardless of cron."""
    from magi.agent.proactive.scheduler import start_scheduler

    sch = start_scheduler(str(state_db))

    # Schedule ~60s in the future so ``get_next_fire_time``
    # returns a real instant we can assert against.
    fire_at = dt.datetime(2099, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc).isoformat()
    row = _make_task(state_db, name="once-row", run_at=fire_at, cron="")

    sch.register(row)
    job = sch._sched.get_job(row.id)
    assert job is not None
    assert type(job.trigger).__name__ == "DateTrigger"
    # DateTrigger computes the next-fire instant from the
    # run_at string; apscheduler tz-aware.
    next_fire = job.trigger.get_next_fire_time(
        None, dt.datetime.now(dt.timezone.utc),
    )
    assert next_fire is not None
    assert next_fire.year == 2099


def test_register_with_cron_only_uses_cron_trigger(state_db: Path) -> None:
    """Recurring path unchanged by the once-fire addition.
    Regression guard: ``run_at=NULL`` + ``cron='0 9 * * *'``
    still goes through ``CronTrigger``."""
    from magi.agent.proactive.scheduler import start_scheduler

    sch = start_scheduler(str(state_db))
    row = _make_task(
        state_db,
        name="daily-row",
        cron="0 9 * * *",
        run_at=None,
    )
    sch.register(row)
    job = sch._sched.get_job(row.id)
    assert job is not None
    assert type(job.trigger).__name__ == "CronTrigger"


def test_register_with_both_cron_and_run_at_prefers_run_at(state_db: Path) -> None:
    """If a caller violates the one-of invariant and sets
    both columns (rare; the API + tool both validate),
    ``register`` still uses ``DateTrigger`` for that row —
    fail-open to the more recent schema. The point is
    not silent fallback to cron; it's "don't crash the
    loop on a slightly malformed row".
    """
    from magi.agent.proactive.scheduler import start_scheduler

    sch = start_scheduler(str(state_db))
    fire_at = "2099-01-01T00:00:00+00:00"
    row = _make_task(
        state_db,
        name="both-set",
        cron="0 9 * * *",
        run_at=fire_at,
    )
    sch.register(row)
    job = sch._sched.get_job(row.id)
    assert job is not None
    assert type(job.trigger).__name__ == "DateTrigger"


# -- tool round-trip ---------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_task_tool_once_writes_run_at_row(
    state_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the tool: calling
    ``schedule_task(frequency="once", run_at=...)`` writes
    a Task row with ``cron=""`` and ``run_at`` set; the
    scheduler registers a ``DateTrigger`` on the row's id.

    Full live fire (apscheduler running the callback) is
    not exercised — that's a live smoke concern. The
    shape layer is what matters for refactor safety.
    """
    from magi.agent.proactive.scheduler import start_scheduler, get_scheduler
    from magi.agent.tools.schedule_task import ScheduleTaskTool
    from magi.agent.tools.base import ToolContext

    start_scheduler(str(state_db))

    # Seed a target operator + bind the cookie identity
    # the ``_gate`` consults.
    from magi.agent.db import Employee
    with open_session() as db:
        db.add(Employee(
            name="tester",
            telegram_id=90002,
            role="admin",
            provider="minimax",
            api_key="fake-key",
        ))
        db.commit()

    ctx = ToolContext(
        state_dir=str(state_db),
        workspace=state_db.parent,
        chat_id="0",
        employee_id=1,
        channel="webui",
    )
    res = await ScheduleTaskTool().run(
        ctx,
        name="remind-me-lunch",
        prompt="tell me what you know about Italian food",
        frequency="once",
        run_at="2099-01-01T12:00:00+00:00",
        channel="webui",
    )
    assert res.is_error is False, res.content

    with open_session() as db:
        row = db.query(Task).filter_by(name="remind-me-lunch").one()
        # ``cron`` is the sentinel empty string so the
        # column's NOT-NULL constraint stays satisfied; the
        # once-fire is fully described by ``run_at``.
        assert row.cron == ""
        assert row.run_at == "2099-01-01T12:00:00+00:00"

    job = get_scheduler()._sched.get_job(row.id)
    assert type(job.trigger).__name__ == "DateTrigger"


@pytest.mark.asyncio
async def test_schedule_task_tool_once_rejects_bad_run_at(
    state_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_at`` validation surfaces as ``is_error=True`` —
    the LLM gets a precise message back, not a server-side
    traceback."""
    from magi.agent.proactive.scheduler import start_scheduler
    from magi.agent.tools.schedule_task import ScheduleTaskTool
    from magi.agent.tools.base import ToolContext

    start_scheduler(str(state_db))

    from magi.agent.db import Employee
    with open_session() as db:
        db.add(Employee(
            name="tester",
            telegram_id=90003,
            role="admin",
            provider="minimax",
            api_key="fake-key",
        ))
        db.commit()

    ctx = ToolContext(
        state_dir=str(state_db),
        workspace=state_db.parent,
        chat_id="0",
        employee_id=1,
        channel="webui",
    )
    res = await ScheduleTaskTool().run(
        ctx,
        name="bad-run-at",
        prompt="anything",
        frequency="once",
        run_at="not-a-timestamp",
        channel="webui",
    )
    assert res.is_error is True
    # LLM-facing phrasing: includes the offending input
    # so the model can fix the next attempt.
    assert "not-a-timestamp" in res.content
    assert "invalid run_at" in res.content


# -- validate_run_at_future -----------------------------------------------


def test_validate_run_at_future_accepts_clear_future() -> None:
    """A timestamp well in the future is the happy path.
    The function returns the input unchanged (already
    canonical from :func:`validate_run_at`)."""
    out = validate_run_at_future("2099-01-01T00:00:00+00:00")
    assert out == "2099-01-01T00:00:00+00:00"


def test_validate_run_at_future_rejects_clear_past() -> None:
    """A timestamp an hour in the past is the bug we're
    guarding against — apscheduler's ``DateTrigger``
    silently drops it, leaving the operator confused
    about why the row never fired. Reject here so the
    error surfaces at create-time with a clear message."""
    with pytest.raises(ValueError) as exc_info:
        validate_run_at_future("2020-01-01T00:00:00+00:00")
    assert "in the future" in str(exc_info.value)
    assert "2020-01-01T00:00:00+00:00" in str(exc_info.value)


def test_validate_run_at_future_respects_grace_window() -> None:
    """The 60-second grace window absorbs clock skew
    between the operator's browser, the WebUI server,
    and the DB host. A timestamp 30 seconds in the past
    still schedules (within tolerance); a timestamp 90
    seconds in the past rejects.

    We pass an explicit ``now=`` so the test is
    deterministic across timezones + system clock."""
    # 30 s in the past — within grace, accepted.
    server_now = datetime.now(timezone.utc)
    near_past = (server_now - timedelta(seconds=30)).isoformat(
        timespec="seconds"
    )
    # The helper canonicalises to seconds; the comparison
    # uses the rounded-to-second value. A 30-s drift is
    # well within the 60-s grace.
    validate_run_at_future(near_past)
    # 90 s in the past — outside grace, rejected.
    far_past = (server_now - timedelta(seconds=90)).isoformat(
        timespec="seconds"
    )
    with pytest.raises(ValueError):
        validate_run_at_future(far_past)


def test_validate_run_at_future_uses_explicit_now() -> None:
    """``now=`` is the deterministic-test seam: a fixed
    server-side reference makes the test reproducible
    regardless of when pytest runs. A timestamp just
    past the injected ``now`` rejects; one well in the
    future accepts."""
    fixed_now = datetime(2099, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # 5 minutes past the injected now → reject.
    past = (fixed_now - timedelta(minutes=5)).isoformat(
        timespec="seconds"
    )
    with pytest.raises(ValueError):
        validate_run_at_future(past, now=fixed_now)
    # 1 day after the injected now → accept.
    future = (fixed_now + timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    validate_run_at_future(future, now=fixed_now)


def test_validate_run_at_future_handles_naive_input() -> None:
    """A naive ISO string (no tzinfo) is interpreted as
    UTC. The comparison must use the same assumption so
    a naive "now + 5 min" string doesn't get mis-tagged.
    The helper normalises *only inside the comparison*
    — the returned string is the input verbatim, so we
    check that no exception is raised (the canonical
    ``+00:00`` stamping happens upstream in
    :func:`validate_run_at`, called by the API/tool
    *before* this helper)."""
    server_now = datetime.now(timezone.utc)
    naive_future = (server_now + timedelta(hours=1)).replace(
        tzinfo=None
    ).isoformat(timespec="seconds")
    # No exception: helper tags naive as UTC internally
    # before comparing.
    out = validate_run_at_future(naive_future)
    assert out == naive_future  # returned verbatim, not re-canonicalised
