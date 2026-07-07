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

logger = logging.getLogger("magi.runtime.proactive.cron_utils")


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
