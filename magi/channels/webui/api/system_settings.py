"""System-level config: timezone.

This is a per-MAGI-node setting (Adam has its own, every
EVE has its own). Stored in the same ``settings`` meta-key
table that already holds ``tg.read_reaction_emoji`` and the
bot token, so it inherits the existing
``state_get`` / ``state_set`` / WAL concurrency story.

Today the only consumer is the token-bill aggregation
endpoint, which needs the tz to compute "this week" /
"this month" boundaries. A future C4+ setting
("default LLM model", "max token cap", etc.) lands here
under the same shape.

Why ``zoneinfo`` and not pytz:

- Py 3.9+ stdlib — no dep, no deprecation warnings.
- ``zoneinfo.ZoneInfo(tz)`` raises ``ZoneInfoNotFoundError``
  on an unknown name, which is the exact validation the
  API endpoint needs; no extra logic.
- pytz's localise() footgun (where naive datetimes silently
  get the wrong UTC offset) doesn't apply to zoneinfo.

Default is UTC — a sensible choice for a containerized
deploy that hasn't told us where it lives. Operators in
other timezones set this once during setup.
"""

from __future__ import annotations

import logging
import os
import zoneinfo
from typing import Annotated

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from magi.channels.webui.api.departments import AdminGate
from magi.runtime.state.settings import state_get, state_set

logger = logging.getLogger("magi.api.system_settings")

router = APIRouter(tags=["system-settings"])

# Meta key. Single global key (the system itself only has
# one timezone); future "default LLM model" or similar
# settings get their own key in this same module.
SYSTEM_TZ_KEY = "system.timezone"
DEFAULT_TIMEZONE = "UTC"


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


def get_system_timezone(state_dir: str) -> str:
    """Return the configured timezone name (e.g. ``"UTC"``,
    ``"Asia/Shanghai"``).

    Falls back to :data:`DEFAULT_TIMEZONE` (UTC) when the
    stored value is empty / invalid. Validation runs
    through :class:`zoneinfo.ZoneInfo` so a hand-edited
    garbage value can't crash the aggregation endpoint.
    """
    raw = state_get(state_dir, SYSTEM_TZ_KEY)
    if not raw:
        return DEFAULT_TIMEZONE
    try:
        zoneinfo.ZoneInfo(raw)
    except Exception:
        logger.warning(
            "system.timezone stored value %r is not a valid IANA tz; "
            "falling back to %s",
            raw, DEFAULT_TIMEZONE,
        )
        return DEFAULT_TIMEZONE
    return raw


def set_system_timezone(state_dir: str, tz: str) -> None:
    """Persist a new timezone.

    Validates via :class:`zoneinfo.ZoneInfo`; raises
    :class:`zoneinfo.ZoneInfoNotFoundError` on an unknown
    name. Caller (the API handler) maps that to a 400.
    """
    zoneinfo.ZoneInfo(tz)  # raises on invalid
    state_set(state_dir, SYSTEM_TZ_KEY, tz)


class TimezoneOut(BaseModel):
    """``GET /api/system-settings/timezone`` response.

    ``current`` is what the aggregation endpoint will read
    on the next request — a Save here affects the next
    ``GET /api/employees/{id}/token-usage`` call. ``choices``
    is the dropdown the UI renders; the full IANA tz
    database, sorted, no grouping (v0 doesn't have a
    preferences panel to organise them by region).
    """

    current: str
    default: str
    choices: list[str]


class TimezoneUpdateRequest(BaseModel):
    """``PUT /api/system-settings/timezone`` body."""

    timezone: str = Field(min_length=1, max_length=64)


@router.get("/system-settings/timezone", response_model=TimezoneOut)
def get_system_timezone_endpoint(_admin: AdminGate) -> TimezoneOut:
    return TimezoneOut(
        current=get_system_timezone(_state_dir()),
        default=DEFAULT_TIMEZONE,
        # Sort so the UI dropdown has a stable, alphabetical
        # order — no preference for "common first", v0 keeps
        # the surface uniform.
        choices=sorted(zoneinfo.available_timezones()),
    )


@router.put("/system-settings/timezone", response_model=TimezoneOut)
def put_system_timezone(
    payload: TimezoneUpdateRequest,
    _admin: AdminGate,
) -> TimezoneOut:
    """Persist a new system timezone.

    Validates against the IANA tz database; an unknown
    name returns 400 ``validation.unknown_timezone`` so the
    operator gets a clear hint instead of a silent fall-
    back to UTC.
    """
    from magi.channels.webui.api.errors import MagiHTTPException

    tz = payload.timezone
    try:
        set_system_timezone(_state_dir(), tz)
    except zoneinfo.ZoneInfoNotFoundError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.unknown_timezone",
            detail=f"timezone {tz!r} is not a valid IANA tz name",
        )
    logger.info("system.timezone set to %r", tz)
    return TimezoneOut(
        current=tz,
        default=DEFAULT_TIMEZONE,
        choices=sorted(zoneinfo.available_timezones()),
    )


# ────────────────────────────────────────────────────────────────── #
# Tool-loop max iterations (D.16)
# ────────────────────────────────────────────────────────────────── #
#
# Caps how many times the agent loop will call the LLM
# inside a single chat turn when the model keeps asking
# for more tools. Each iteration is one round-trip
# (Anthropic call + tool execution + next request), so the
# cap also bounds the wall-clock cost of one chat turn.
#
# Default 10: enough for a typical "read X, then Y, then
# reply" tool chain (3-4 iterations typical); high enough
# that an ambitious "search the codebase and write a
# summary" flow isn't artificially clipped.
#
# Hard cap 50 in the API: a runaway agent calling 100+
# tools would burn through the LLM quota; even 50 is on
# the order of 100s of seconds, which is way past
# reasonable for a single chat reply.

TOOL_MAX_ITERATIONS_KEY = "system.tool_max_iterations"
DEFAULT_TOOL_MAX_ITERATIONS = 10
MAX_TOOL_MAX_ITERATIONS = 50
MIN_TOOL_MAX_ITERATIONS = 1


def get_tool_max_iterations(state_dir: str) -> int:
    """Return the configured max tool iterations.

    Falls back to :data:`DEFAULT_TOOL_MAX_ITERATIONS` (10)
    when the stored value is missing / non-numeric /
    outside ``[MIN, MAX]``. The bounds-clamp is defensive
    — a hand-edited 0 would mean "agent can never call
    any tool", which would silently break the LLM's
    tool-use loop. We don't want that.
    """
    raw = state_get(state_dir, TOOL_MAX_ITERATIONS_KEY)
    try:
        value = int(raw) if raw is not None else DEFAULT_TOOL_MAX_ITERATIONS
    except (TypeError, ValueError):
        logger.warning(
            "system.tool_max_iterations stored value %r is not a number; "
            "falling back to default %d",
            raw, DEFAULT_TOOL_MAX_ITERATIONS,
        )
        return DEFAULT_TOOL_MAX_ITERATIONS
    if value < MIN_TOOL_MAX_ITERATIONS or value > MAX_TOOL_MAX_ITERATIONS:
        logger.warning(
            "system.tool_max_iterations stored value %d is outside "
            "[%d, %d]; clamping",
            value, MIN_TOOL_MAX_ITERATIONS, MAX_TOOL_MAX_ITERATIONS,
        )
        return max(MIN_TOOL_MAX_ITERATIONS, min(MAX_TOOL_MAX_ITERATIONS, value))
    return value


class ToolMaxIterationsOut(BaseModel):
    """``GET /api/system-settings/tool-max-iterations`` response."""

    current: int
    default: int
    min: int
    max: int


class ToolMaxIterationsUpdateRequest(BaseModel):
    """``PUT /api/system-settings/tool-max-iterations`` body."""

    value: int = Field(ge=MIN_TOOL_MAX_ITERATIONS, le=MAX_TOOL_MAX_ITERATIONS)


@router.get(
    "/system-settings/tool-max-iterations",
    response_model=ToolMaxIterationsOut,
)
def get_tool_max_iterations_endpoint(_admin: AdminGate) -> ToolMaxIterationsOut:
    return ToolMaxIterationsOut(
        current=get_tool_max_iterations(_state_dir()),
        default=DEFAULT_TOOL_MAX_ITERATIONS,
        min=MIN_TOOL_MAX_ITERATIONS,
        max=MAX_TOOL_MAX_ITERATIONS,
    )


@router.put(
    "/system-settings/tool-max-iterations",
    response_model=ToolMaxIterationsOut,
)
def put_tool_max_iterations(
    payload: ToolMaxIterationsUpdateRequest,
    _admin: AdminGate,
) -> ToolMaxIterationsOut:
    """Persist a new max tool iterations value.

    Validation is Pydantic-side (``Field(ge=MIN, le=MAX)``):
    a value outside the bounds returns 422 with Pydantic's
    structured error before this handler runs. We don't
    need to re-validate here.
    """
    from magi.runtime.state.settings import state_set as _state_set
    _state_set(_state_dir(), TOOL_MAX_ITERATIONS_KEY, str(payload.value))
    logger.info("system.tool_max_iterations set to %d", payload.value)
    return ToolMaxIterationsOut(
        current=payload.value,
        default=DEFAULT_TOOL_MAX_ITERATIONS,
        min=MIN_TOOL_MAX_ITERATIONS,
        max=MAX_TOOL_MAX_ITERATIONS,
    )