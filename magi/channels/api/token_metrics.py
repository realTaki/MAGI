"""Per-contact aggregated metrics: token usage.

The single endpoint returns week / month / total aggregates
in one call so the detail panel can render all three rows
without three round-trips. Period boundaries are computed
in the operator-configured timezone (see
``system_settings.get_system_timezone``) — week = Mon-Sun,
month = 1st of month, both inclusive of "now".

The data source is the ``token_usage`` table
(``magi.db.TokenUsage``) — one row per
outbound LLM call, written by the providers worker (which is
closest to the usage data) right after a successful call. The
aggregation is a single SQL
``SELECT SUM(...)`` per period, no Python-side scan.

Why all three periods in one response:

- Saves the dashboard a per-render waterfall of three
  ``fetch()`` calls.
- Keeps the SQL pattern uniform (one query per period,
  same shape).
- Avoids three ORM ``query`` objects open at the same
  time when an admin opens a busy contact's detail panel.

Week / month boundaries use ``zoneinfo`` (Py 3.9+ stdlib)
— pytz's localize/normalize footgun doesn't apply here
because we construct local datetimes directly and convert
to UTC for the SQL comparison.
"""

from __future__ import annotations

import logging
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep

logger = logging.getLogger("magi.api.token_metrics")

router = APIRouter(tags=["token-metrics"])


@dataclass(frozen=True)
class PeriodBounds:
    """Inclusive start, inclusive end. ``end`` is "now" so
    the operator can see the running total growing in real
    time as new chat turns land."""

    start: datetime  # tz-aware in the configured tz
    end: datetime  # tz-aware in the configured tz


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
        monday_local = now_local - timedelta(days=now_local.weekday())
        monday_local = monday_local.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return PeriodBounds(start=monday_local, end=now_local)
    if period == "month":
        first_local = now_local.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
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
    period_start: datetime
    period_end: datetime


def _aggregate_period(
    bus,
    contact_id: int,
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
    start_utc_naive = bounds.start.astimezone(UTC).replace(tzinfo=None)
    end_utc_naive = bounds.end.astimezone(UTC).replace(tzinfo=None)

    in_sum, out_sum, calls = bus.token_usage_book.aggregate(
        contact_id=contact_id,
        start=start_utc_naive,
        end=end_utc_naive,
    )

    return PeriodUsage(
        input_tokens=int(in_sum or 0),
        output_tokens=int(out_sum or 0),
        call_count=int(calls or 0),
        period_start=bounds.start,
        period_end=bounds.end,
    )


class PeriodUsageOut(BaseModel):
    """Per-period totals. One of the three keys in
    :class:`TokenUsageOut`."""

    input_tokens: int
    output_tokens: int
    call_count: int
    period_start: datetime
    period_end: datetime


class TokenUsageOut(BaseModel):
    """``GET /api/contacts/{contact_id}/token-usage`` response.

    All three periods in one response — the dashboard's
    detail panel renders three rows; one round-trip.
    """

    contact_id: int
    week: PeriodUsageOut
    month: PeriodUsageOut
    total: PeriodUsageOut
    timezone: str  # echoed so the UI can show the active tz


@router.get(
    "/contacts/{contact_id}/token-usage",
    response_model=TokenUsageOut,
)
def get_contact_token_usage(
    contact_id: int,
    _admin: AdminGate,
    bus: BusDep,
) -> TokenUsageOut:
    """Aggregate token usage for one contact across three
    periods.

    All three queries run against the same connection in
    sequence — each one is bounded by the
    ``(contact_id, ts)`` composite index, so a busy
    contact with thousands of calls is still O(rows in
    window), not O(total rows).
    """
    tz_name = bus.settings_book.get_value(key="system.timezone") or "UTC"
    tz = zoneinfo.ZoneInfo(tz_name)

    week = _aggregate_period(bus, contact_id, "week", tz)
    month = _aggregate_period(bus, contact_id, "month", tz)
    total = _aggregate_period(bus, contact_id, "total", tz)

    return TokenUsageOut(
        contact_id=contact_id,
        week=PeriodUsageOut(**week.__dict__),
        month=PeriodUsageOut(**month.__dict__),
        total=PeriodUsageOut(**total.__dict__),
        timezone=tz_name,
    )
