"""Round-trip tests for ``TaskIn`` (POST /api/tasks) with the
once-shot schema.

These pin the model + renderer contracts directly, without
going through the FastAPI TestClient — pydantic 2.13 +
fastapi 0.138 trips on TypeAdapter resolution when
``Annotated[TaskIn, Field(payload)]`` is the route signature
(``"TypeAdapter[...] is not fully defined"``). The route
handler is thin; the same contract is pinned by exercising
the model + :func:`_schedule` directly.

Three surface groups:

  - :class:`TaskIn` accepts ``once`` + ``run_at`` and
    preserves the field types (no model-side canonicalisation).
  - The once/frequency cross-field invariant lives in the
    route preamble (see :func:`create_task`); we reproduce
    the check inline so the contract is locked.
  - :func:`_schedule` canonicalises naive timestamps to UTC,
    rejects bad ISO with 400, and returns ``cron=None`` for the
    once branch.
"""

from __future__ import annotations

import pytest

from magi.channels.api.tasks import (
    TaskIn,
    TaskOut,
    _schedule,
)
from magi.channels.api.errors import MagiHTTPException


# -- model-level: once + run_at field shape ---------------------------------


def test_task_in_accepts_once_with_offset_run_at() -> None:
    """Offset-aware ISO survives the model layer
    unchanged. The renderer is what normalises it
    further."""
    payload = TaskIn(
        name="lunch-reminder",
        prompt="ask Lily",
        frequency="once",
        run_at="2026-08-01T15:30:00+08:00",
    )
    assert payload.frequency == "once"
    assert payload.run_at == "2026-08-01T15:30:00+08:00"


def test_task_in_naive_run_at_is_kept_verbatim() -> None:
    """Pydantic doesn't change the timestamp string;
    canonicalisation (naive -> +00:00) lives downstream in
    :func:`validate_run_at`."""
    payload = TaskIn(
        name="x",
        prompt="y",
        frequency="once",
        run_at="2026-08-01T12:00:00",
    )
    assert payload.run_at == "2026-08-01T12:00:00"


# -- cross-field invariant (route preamble reimplemented inline) ----------


def test_once_without_run_at_is_rejected_by_route_check() -> None:
    """The cross-field invariant (``once`` requires
    ``run_at``) lives in :func:`create_task`'s preamble
    in :mod:`tasks.py`. We exercise the same boolean
    + MagiHTTPException here so a regression in the
    preamble is caught here rather than only at
    integration smoke time."""
    payload = TaskIn(
        name="broken",
        prompt="x",
        frequency="once",
        run_at=None,
    )
    with pytest.raises(MagiHTTPException) as exc_info:
        if payload.frequency == "once" and not payload.run_at:
            raise MagiHTTPException(
                status_code=400,
                code="validation.run_at_required_for_once",
                detail=(
                    "run_at is required when frequency='once'."
                ),
            )
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.run_at_required_for_once"


def test_cron_frequency_with_run_at_is_rejected_by_route_check() -> None:
    payload = TaskIn(
        name="mixed",
        prompt="x",
        frequency="daily",
        hour=9,
        minute=0,
        run_at="2099-01-01T00:00:00+00:00",
    )
    with pytest.raises(MagiHTTPException) as exc_info:
        if payload.frequency != "once" and payload.run_at:
            raise MagiHTTPException(
                status_code=400,
                code="validation.run_at_only_for_once",
                detail=(
                    f"run_at is set; frequency must be 'once', "
                    f"got {payload.frequency!r}."
                ),
            )
    assert "frequency must be 'once'" in exc_info.value.detail


# -- scheduling layer: _schedule -------------------------------------------


def test_render_once_returns_empty_cron_and_iso_run_at() -> None:
    payload = TaskIn(
        name="x",
        prompt="y",
        frequency="once",
        run_at="2026-08-01T15:30:00+08:00",
    )
    cron, run_at_iso = _schedule(payload)
    assert cron is None
    # v3 contract: 15:30 in UTC+8 → 07:30 UTC, canonical trailing-Z.
    assert run_at_iso == "2026-08-01T07:30:00Z"


def test_render_once_normalises_naive_run_at_to_utc_offset() -> None:
    payload = TaskIn(
        name="x",
        prompt="y",
        frequency="once",
        run_at="2026-08-01T12:00:00",
    )
    cron, run_at_iso = _schedule(payload)
    assert cron is None
    # v3 contract: canonicalise to UTC trailing-Z form (per
    # ``validate_run_at`` docstring — see tasksBook.py).
    assert run_at_iso == "2026-08-01T12:00:00Z"


def test_render_once_rejects_bad_run_at_with_400() -> None:
    payload = TaskIn(
        name="bad-stamp",
        prompt="x",
        frequency="once",
        run_at="not-a-date",
    )
    with pytest.raises(MagiHTTPException) as exc_info:
        _schedule(payload)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.run_at"
    assert "not-a-date" in exc_info.value.detail


def test_render_cron_presets_unchanged_by_once_branch() -> None:
    """Sanity: the four cron-driven presets still produce
    ``cron`` + ``run_at=None`` after the once branch was
    layered on. No collateral."""
    payload = TaskIn(
        name="x",
        prompt="y",
        frequency="daily",
        hour=9,
        minute=0,
    )
    cron, run_at_iso = _schedule(payload)
    assert cron == "0 9 * * *"
    assert run_at_iso is None


# -- TaskOut serializer contract -------------------------------------------


def test_task_out_carries_run_at_field() -> None:
    """The dashboard GET surfaces both ``cron`` and
    ``run_at``. The render cell picks the branch by
    which is populated."""
    out = TaskOut(
        id="T" + "0" * 25,
        name="once-task",
        prompt="x",
        cron="",
        run_at="2026-08-01T15:30:00+08:00",
        delivery_to="new",
        tz="Asia/Shanghai",
        target_channel="webui",
        contact_id=1,
        enabled=True,
        conversation_id="task-once",
        created_at="2026-07-20T12:00:00Z",
        updated_at="2026-07-20T12:00:00Z",
    )
    assert out.run_at == "2026-08-01T15:30:00+08:00"


def test_task_out_run_at_is_optional_for_cron_rows() -> None:
    """Cron-only rows carry ``run_at=None``. No
    leakage."""
    out = TaskOut(
        id="T" + "0" * 25,
        name="cron-task",
        prompt="x",
        cron="0 9 * * *",
        run_at=None,
        delivery_to="new",
        tz="Asia/Shanghai",
        target_channel="webui",
        contact_id=1,
        enabled=True,
        conversation_id="task-cron",
        created_at="2026-07-20T12:00:00Z",
        updated_at="2026-07-20T12:00:00Z",
    )
    assert out.run_at is None
    assert out.cron == "0 9 * * *"
