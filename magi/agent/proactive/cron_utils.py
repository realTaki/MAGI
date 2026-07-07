"""Cron expression helpers — thin wrapper around APScheduler.

APScheduler ships ``CronTrigger.from_crontab()`` which already
parses 5-field (minute / hour / day / month / dow) cron plus
the special shortcuts (``@hourly``, ``@daily``, …). This
module just exposes the bits the runner / API / tool need:

- :func:`validate_cron` raises ``ValueError`` on bad input;
  used by both the API router's Pydantic validator and
  the ``schedule_task`` tool. The point of the helper is
  one canonical message and a single parse call site.
- :func:`next_fire` computes the upcoming fire time in the
  task's tz. ``None`` if APScheduler can't read the cron
  (caller already validated, so this is a defensive
  fallback).
- :func:`humanize_cron` returns a short English phrase
  (e.g. ``"Every weekday at 5 PM"``) used by the WebUI
  drawer to show the operator what they typed before
  they save. APScheduler 3.11 dropped ``cron_to_str``;
  we walk the trigger's field list and stitch a phrase
  ourselves. The phrasing is a v0 floor — polish later.

We deliberately do NOT write our own cron parser. The
upstream library handles every edge case (last-day-of-
month, ``L``, ``W``, ``#``) and the library's treatment
of those has been battle-tested; re-implementing any
subset here would either miss features or fight the
scheduler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("magi.agent.proactive.cron_utils")


def validate_cron(expr: str) -> None:
    """Raise ``ValueError`` if ``expr`` isn't valid 5-field cron.

    Side effect: import-time ``apscheduler`` is loaded the
    first time this is called — that's the scheduler's
    setup cost the project pays anyway when the
    ``TaskScheduler`` is constructed, so we don't try to
    amortise it here.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("cron expression must be a non-empty string")
    # The trigger's ``__init__`` validates; ``from_crontab``
    # accepts the standard 5 field form AND the @-prefix
    # shortcuts — we accept those too.
    CronTrigger.from_crontab(expr.strip())


def next_fire(expr: str, tz: str = "UTC") -> Optional[datetime]:
    """Return the next fire time of ``expr`` in ``tz``.

    Returns ``None`` on bad input (the API / tool layer
    should have validated first; this is a defensive
    fallback for callers like the WebUI that want to
    preview a fire time without round-tripping through
    the API).
    """
    try:
        CronTrigger.from_crontab(expr, timezone=tz)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    # APScheduler's ``get_next_fire_time`` is timezone-aware
    # when given a tz-aware ``now`` — pass it through so
    # the math lands in the task's tz, not the host's.
    return CronTrigger.from_crontab(expr, timezone=tz).get_next_fire_time(
        None,
        datetime.now(timezone.utc).astimezone(zone),
    )


def humanize_cron(expr: str) -> str:
    """Render a one-line English phrase for ``expr``.

    v0 only — covers the common cases the operator will
    reach for (``* * * * *``, ``0 9 * * *``, ``*/5 * * * *``,
    weekday/weekend blocks). For complex expressions
    fall back to the raw string so we never invent a
    misleading hint.
    """
    try:
        trigger = CronTrigger.from_crontab(expr)
    except ValueError:
        return expr or "(empty)"
    fields = {f.name: str(f) for f in trigger.fields}
    minute = fields.get("minute", "*")
    hour = fields.get("hour", "*")
    dow = fields.get("day_of_week", "*")
    dom = fields.get("day", "*")
    month = fields.get("month", "*")

    # Common short-circuits first to keep the phrase tight.
    # ``* * * * *`` is fire-every-minute; show that as
    # "Every minute" rather than the per-field expansion
    # which reads like a cron spec back to the operator.
    all_star = all(v in ("*", None) for v in (minute, hour, dom, month, dow))
    if all_star:
        return "Every minute"

    # Hourly
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "Every hour"
    # Daily at HH:MM
    if (
        dom == "*"
        and month == "*"
        and dow == "*"
        and minute.isdigit()
        and hour.isdigit()
    ):
        return f"Every day at {int(hour):02d}:{int(minute):02d}"
    # Weekdays / weekends
    if dom == "*" and month == "*":
        if dow == "mon-fri":
            return (
                f"Weekdays at "
                f"{_format_hhmm(hour, minute)}"
                if not (minute == "*" and hour == "*")
                else "Weekdays, every minute"
            )
        if dow == "sat,sun":
            return (
                f"Weekends at {_format_hhmm(hour, minute)}"
                if not (minute == "*" and hour == "*")
                else "Weekends, every minute"
            )
    # Fall back to the raw expression — better to show a
    # spec than a wrong humanisation.
    return expr


def _format_hhmm(hour: str, minute: str) -> str:
    """``"5"`` + ``"0"`` -> ``"5:00"``; ``"17"`` + ``"30"`` -> ``"17:30"``.

    Falls back to the raw field if either side isn't a
    plain int (we already short-circuited the all-star
    and hourly cases above; this helper is only called
    from the weekday / weekend branch).
    """
    try:
        return f"{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        return f"{hour}:{minute}"


# ──────────────────────────────────────────────────────────────────────── #
# Preset builder — the WebUI / API surfaces an enum (`hourly` / `daily` /
# `weekly` / `monthly`) plus a small set of moment fields (HH:MM,
# day-of-week, day-of-month, minute-of-hour). ``preset_to_cron``
# stitches those into the 5-field cron string apscheduler expects.
#
# Why not just let the user type cron? In the user's words:
# "不要让用户自己写 cron". The Pydantic validator still runs
# the result through ``validate_cron`` so bad input from a stale
# client doesn't silently land.
# ──────────────────────────────────────────────────────────────────────── #

from typing import Literal, Optional

CronFrequency = Literal["hourly", "daily", "weekly", "monthly"]


def preset_to_cron(
    frequency: CronFrequency,
    *,
    hour: int = 0,
    minute: int = 0,
    day_of_week: Optional[int] = None,
    day_of_month: Optional[int] = None,
) -> str:
    """Render the preset + moment fields into a 5-field cron.

    Mapping (minute / hour / day / month / dow):

    - hourly:  ``M  * * * *`` — fires every minute the hour rolls.
                 Caller passes ``minute`` for "fire at minute X
                 past every hour"; hour is ignored.
    - daily:   ``M H * * *`` — fires once at HH:MM every day.
    - weekly:  ``M H * * DOW`` — fires once at HH:MM on one DOW
                 (Python ``datetime.weekday()``, 0=Mon..6=Sun;
                 cron uses 0=Sun..6=Sat so we translate).
    - monthly: ``M H DOM * *`` — fires once at HH:MM on the
                 given DOM (1..31).

    Hour must be 0..23, minute 0..59, DOM 1..31, DOW 0..6
    (``weekday()`` style with Monday=0; we shift to cron style
    on output). Invalid combinations raise ``ValueError``.

    The point is NOT to ship every cron edge case (ranges,
    lists, ``*/N``) in v0. The 4 presets above cover the
    common cases the operator reaches for. The raw ``cron``
    column survives in the DB / API for future expansion
    without a migration.
    """
    if not (0 <= int(minute) <= 59):
        raise ValueError(f"minute must be 0..59, got {minute!r}")
    if not (0 <= int(hour) <= 23):
        raise ValueError(f"hour must be 0..23, got {hour!r}")
    m = int(minute)
    h = int(hour)
    if frequency == "hourly":
        return f"{m} * * * *"
    if frequency == "daily":
        return f"{m} {h} * * *"
    if frequency == "weekly":
        if day_of_week is None:
            raise ValueError("weekly preset requires day_of_week (0..6, Mon=0)")
        if not (0 <= int(day_of_week) <= 6):
            raise ValueError(f"day_of_week must be 0..6, got {day_of_week!r}")
        # Convert Python's Mon=0..Sun=6 to cron's Sun=0..Sat=6.
        cron_dow = (int(day_of_week) + 1) % 7
        return f"{m} {h} * * {cron_dow}"
    if frequency == "monthly":
        if day_of_month is None:
            raise ValueError("monthly preset requires day_of_month (1..31)")
        if not (1 <= int(day_of_month) <= 31):
            raise ValueError(f"day_of_month must be 1..31, got {day_of_month!r}")
        return f"{m} {h} {int(day_of_month)} * *"
    raise ValueError(f"unknown frequency: {frequency!r}")

