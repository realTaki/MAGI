"""Per-employee aggregated metrics: token usage.

The single endpoint returns week / month / total aggregates
in one call so the detail panel can render all three rows
without three round-trips. Period boundaries are computed
in the operator-configured timezone (see
``system_settings.get_system_timezone``) — week = Mon-Sun,
month = 1st of month, both inclusive of "now".

The data source is the ``token_usage`` table
(``magi.agent.db.TokenUsage``) — one row per
outbound LLM call, written by ``agent._record_token_usage``
after the audit row. The aggregation is a single SQL
``SELECT SUM(...)`` per period, no Python-side scan.

Why all three periods in one response:

- Saves the dashboard a per-render waterfall of three
  ``fetch()`` calls.
- Keeps the SQL pattern uniform (one query per period,
  same shape).
- Avoids three ORM ``query`` objects open at the same
  time when an admin opens a busy employee's detail panel.

Week / month boundaries use ``zoneinfo`` (Py 3.9+ stdlib)
— pytz's localize/normalize footgun doesn't apply here
because we construct local datetimes directly and convert
to UTC for the SQL comparison.
"""

from __future__ import annotations

import logging
import os
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.system_settings import get_system_timezone
from magi.agent.db import TokenUsage, open_session, require_state_dir

logger = logging.getLogger("magi.api.employee_metrics")

router = APIRouter(tags=["employee-metrics"])


def _state_dir() -> str:
    return require_state_dir()


@dataclass(frozen=True)
class PeriodBounds:
    """Inclusive start, inclusive end. ``end`` is "now" so
    the operator can see the running total growing in real
    time as new chat turns land."""

    start: datetime  # tz-aware in the configured tz
    end: datetime    # tz-aware in the configured tz


def _period_bounds(period: str, tz: zoneinfo.ZoneInfo) -> PeriodBounds:
    """Compute the (start, end) for a given period in the
    given timezone.

    - ``week``  : Mon 00:00 local → now.
    - ``month`` : 1st of month 00:00 local → now.
    - ``total`` : 1970-01-01 → now (effectively "all rows").
    """
    now_local = datetime.now(tz=tz)
    if period == "total":
        return PeriodBounds(
            start=datetime(1970, 1, 1, tzinfo=tz),
            end=now_local,
        )
    if period == "week":
        # ``weekday()`` is 0 for Monday — subtract that many
        # days to land on the most recent Monday. Both ends
        # are 00:00 local; the "end" is the current instant
        # so today's chat turns are included.
        monday_local = now_local - timedelta(days=now_local.weekday())
        monday_local = monday_local.replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return PeriodBounds(start=monday_local, end=now_local)
    if period == "month":
        first_local = now_local.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        return PeriodBounds(start=first_local, end=now_local)
    raise ValueError(f"unknown period: {period!r}")


@dataclass(frozen=True)
class PeriodUsage:
    """One row in the API response.

    Matches what the detail panel renders: input / output
    token totals + the number of LLM calls in the window.
    ``period_start`` / ``period_end`` are echoed back so the
    UI can show "Mon 00:00 → Fri 17:32" in tooltips without
    re-deriving them on the client.
    """

    input_tokens: int
    output_tokens: int
    call_count: int
    period_start: str  # ISO 8601
    period_end: str


def _aggregate_period(
    state_dir: str,
    employee_id: int,
    period: str,
    tz: zoneinfo.ZoneInfo,
) -> PeriodUsage:
    """Run one ``SELECT SUM(...)`` for the given period.

    The ``ts`` column is naive UTC, so the bounds (which
    are tz-aware) are converted to UTC and stripped of
    tzinfo before the comparison. Storing UTC + a configured
    tz means we never embed the user's local time into
    the row itself — every row is comparable, and only
    the aggregation logic cares about timezone math.
    """
    bounds = _period_bounds(period, tz)
    # Convert the tz-aware bounds to naive UTC so they
    # match the ``DateTime`` column's storage convention.
    # astimezone(UTC) gives a tz-aware UTC datetime;
    # ``replace(tzinfo=None)`` strips it for the SQL bind.
    start_utc_naive = bounds.start.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc_naive = bounds.end.astimezone(timezone.utc).replace(tzinfo=None)

    with open_session() as session:
        in_sum, out_sum, calls = session.execute(
            select(
                func.coalesce(func.sum(TokenUsage.input_tokens), 0),
                func.coalesce(func.sum(TokenUsage.output_tokens), 0),
                func.count(TokenUsage.id),
            ).where(
                TokenUsage.employee_id == employee_id,
                TokenUsage.ts >= start_utc_naive,
                TokenUsage.ts <= end_utc_naive,
            )
        ).one()

    return PeriodUsage(
        input_tokens=int(in_sum or 0),
        output_tokens=int(out_sum or 0),
        call_count=int(calls or 0),
        # Echo the *local* boundaries so the UI can show
        # "本自然周 06-29 00:00 — 07-03 17:32" without doing
        # timezone math client-side.
        period_start=bounds.start.isoformat(),
        period_end=bounds.end.isoformat(),
    )


class PeriodUsageOut(BaseModel):
    """Per-period totals. One of the three keys in
    :class:`TokenUsageOut`."""

    input_tokens: int
    output_tokens: int
    call_count: int
    period_start: str
    period_end: str


class TokenUsageOut(BaseModel):
    """``GET /api/employees/{id}/token-usage`` response.

    All three periods in one response — the dashboard's
    detail panel renders three rows; one round-trip.
    """

    employee_id: int
    week: PeriodUsageOut
    month: PeriodUsageOut
    total: PeriodUsageOut
    timezone: str  # echoed so the UI can show the active tz


@router.get(
    "/employees/{employee_id}/token-usage",
    response_model=TokenUsageOut,
)
def get_employee_token_usage(
    employee_id: int,
    _admin: AdminGate,
) -> TokenUsageOut:
    """Aggregate token usage for one employee across three
    periods.

    All three queries run against the same connection in
    sequence — each one is bounded by the
    ``(employee_id, ts)`` composite index, so a busy
    employee with thousands of calls is still O(rows in
    window), not O(total rows).
    """
    state_dir = _state_dir()
    tz_name = get_system_timezone(state_dir)
    tz = zoneinfo.ZoneInfo(tz_name)

    week = _aggregate_period(state_dir, employee_id, "week", tz)
    month = _aggregate_period(state_dir, employee_id, "month", tz)
    total = _aggregate_period(state_dir, employee_id, "total", tz)

    return TokenUsageOut(
        employee_id=employee_id,
        week=PeriodUsageOut(**week.__dict__),
        month=PeriodUsageOut(**month.__dict__),
        total=PeriodUsageOut(**total.__dict__),
        timezone=tz_name,
    )