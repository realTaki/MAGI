"""System-level config: timezone.

Per-MAGI-node setting (Adam has its own, every EVE has
its own). Stored in the same ``settings`` meta-key
table that already holds ``tg.read_reaction_emoji`` and
the bot token, so it inherits the existing ``state_get``
/ ``state_set`` / WAL concurrency story.

Today the only consumer is the token-bill aggregation
endpoint, which needs the tz to compute "this week" /
"this month" boundaries. A future C4+ setting
("default LLM model", "max token cap", etc.) lands
here under the same shape.

Why ``zoneinfo`` and not pytz:

- Py 3.9+ stdlib — no dep, no deprecation warnings.
- ``zoneinfo.ZoneInfo(tz)`` raises
  ``ZoneInfoNotFoundError`` on an unknown name, which
  is the exact validation the API endpoint needs; no
  extra logic.
- pytz's localise() footgun (where naive datetimes
  silently get the wrong UTC offset) doesn't apply to
  zoneinfo.

Default timezone: used to be a hard-coded
``"UTC"``, forcing every deployer to override
``system.timezone`` before weekly/monthly
aggregations lined up with their wall-clock. Now
resolves lazily to the **server's** local timezone
via :func:`_system_default_timezone` (uses
:mod:`tzlocal`); UTC when the server has no timezone
configured (CI runners). Operators in other
timezones can still set this once during setup via
``PUT /api/system-settings/timezone``.
"""

from __future__ import annotations

import logging
import os
import zoneinfo
from typing import Annotated

from tzlocal import get_localzone

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from magi.channels.webui.api.departments import AdminGate
from magi.agent.db.settings import state_get, state_set
from magi.agent.db.engine import require_state_dir

logger = logging.getLogger("magi.api.system_settings")

router = APIRouter(tags=["system-settings"])

# Meta key. Single global key (the system itself only has
# one timezone); future "default LLM model" or similar
# settings get their own key in this same module.
SYSTEM_TZ_KEY = "system.timezone"


def _system_default_timezone() -> str:
    """Resolve the timezone used when ``system.timezone``
    hasn't been set explicitly.

    We default to the **server's** local timezone — a
    container in Shanghai comes up as ``"Asia/Shanghai"``
    so weekly/monthly aggregations line up with the
    operator's wall-clock without a setup step. CI runners
    with no timezone config fall back to ``"UTC"`` (the
    prior behaviour, preserved).

    The resolution is wrapped in a function (not a
    module-level constant) so the test suite can
    ``monkeypatch.setattr`` it without importing a stale
    value; ``zoneinfo.ZoneInfo`` validates the result
    so a misconfigured system clock still produces an
    IANA name the rest of the stack can parse.
    """
    try:
        return get_localzone().key
    except Exception:
        # Last-resort fallback: ``get_localzone`` can
        # raise on a stripped-down container with no
        # ``/etc/localtime``. UTC is the prior default,
        # so we keep it as the safety net.
        logger.warning(
            "could not resolve server timezone; falling back to UTC"
        )
        return "UTC"


def _state_dir() -> str:
    return require_state_dir()


def get_system_timezone(state_dir: str) -> str:
    """Return the configured timezone name (e.g. ``"UTC"``,
    ``"Asia/Shanghai"``).

    Falls back to :func:`_system_default_timezone` (server's
    local timezone; UTC when unset) when the
    stored value is empty / invalid. Validation runs
    through :class:`zoneinfo.ZoneInfo` so a hand-edited
    garbage value can't crash the aggregation endpoint.
    """
    raw = state_get(state_dir, SYSTEM_TZ_KEY)
    if not raw:
        return _system_default_timezone()
    try:
        zoneinfo.ZoneInfo(raw)
    except Exception:
        logger.warning(
            "system.timezone stored value %r is not a valid IANA tz; "
            "falling back to %s",
            raw, _system_default_timezone(),
        )
        return _system_default_timezone()
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
        default=_system_default_timezone(),
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
        default=_system_default_timezone(),
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
    from magi.agent.db.settings import state_set as _state_set
    _state_set(_state_dir(), TOOL_MAX_ITERATIONS_KEY, str(payload.value))
    logger.info("system.tool_max_iterations set to %d", payload.value)
    return ToolMaxIterationsOut(
        current=payload.value,
        default=DEFAULT_TOOL_MAX_ITERATIONS,
        min=MIN_TOOL_MAX_ITERATIONS,
        max=MAX_TOOL_MAX_ITERATIONS,
    )

# D.17 - auto-compact configuration. Three meta keys
# backed by three helpers. The compaction threshold check
# happens inside agent.handle_message on every chat
# turn (before each LLM call); v0 reads the settings fresh
# on each check so a Save in the UI takes effect
# immediately on the next inbound message.

COMPACT_CONTEXT_WINDOW_KEY = "system.compact_context_window"
COMPACT_THRESHOLD_PCT_KEY = "system.compact_threshold_pct"
COMPACT_KEEP_RECENT_KEY = "system.compact_keep_recent"

DEFAULT_COMPACT_CONTEXT_WINDOW = 100000
DEFAULT_COMPACT_THRESHOLD_PCT = 80
DEFAULT_COMPACT_KEEP_RECENT = 20

MIN_COMPACT_CONTEXT_WINDOW = 16000
MAX_COMPACT_CONTEXT_WINDOW = 200000
MIN_COMPACT_THRESHOLD_PCT = 50
MAX_COMPACT_THRESHOLD_PCT = 95
MIN_COMPACT_KEEP_RECENT = 5
MAX_COMPACT_KEEP_RECENT = 100


def _clamp_int(raw, *, default, lo, hi, label):
    """Parse an int from a meta-key string and clamp to [lo, hi]."""
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "compact config: %s stored value %r is not a number; "
            "falling back to default %d",
            label, raw, default,
        )
        return default
    if v < lo or v > hi:
        logger.warning(
            "compact config: %s stored value %d is outside [%d, %d]; clamping",
            label, v, lo, hi,
        )
        return max(lo, min(hi, v))
    return v


def get_compact_context_window(state_dir):
    return _clamp_int(
        state_get(state_dir, COMPACT_CONTEXT_WINDOW_KEY),
        default=DEFAULT_COMPACT_CONTEXT_WINDOW,
        lo=MIN_COMPACT_CONTEXT_WINDOW,
        hi=MAX_COMPACT_CONTEXT_WINDOW,
        label="context_window",
    )


def get_compact_threshold_pct(state_dir):
    return _clamp_int(
        state_get(state_dir, COMPACT_THRESHOLD_PCT_KEY),
        default=DEFAULT_COMPACT_THRESHOLD_PCT,
        lo=MIN_COMPACT_THRESHOLD_PCT,
        hi=MAX_COMPACT_THRESHOLD_PCT,
        label="threshold_pct",
    )


def get_compact_keep_recent(state_dir):
    return _clamp_int(
        state_get(state_dir, COMPACT_KEEP_RECENT_KEY),
        default=DEFAULT_COMPACT_KEEP_RECENT,
        lo=MIN_COMPACT_KEEP_RECENT,
        hi=MAX_COMPACT_KEEP_RECENT,
        label="keep_recent",
    )


class CompactConfigOut(BaseModel):
    context_window: int
    threshold_pct: int
    keep_recent: int
    default_context_window: int
    default_threshold_pct: int
    default_keep_recent: int


class CompactConfigUpdateRequest(BaseModel):
    context_window: int = Field(
        ge=MIN_COMPACT_CONTEXT_WINDOW, le=MAX_COMPACT_CONTEXT_WINDOW
    )
    threshold_pct: int = Field(
        ge=MIN_COMPACT_THRESHOLD_PCT, le=MAX_COMPACT_THRESHOLD_PCT
    )
    keep_recent: int = Field(
        ge=MIN_COMPACT_KEEP_RECENT, le=MAX_COMPACT_KEEP_RECENT
    )


@router.get("/system-settings/compact-config", response_model=CompactConfigOut)
def get_compact_config(_admin: AdminGate) -> CompactConfigOut:
    state = _state_dir()
    return CompactConfigOut(
        context_window=get_compact_context_window(state),
        threshold_pct=get_compact_threshold_pct(state),
        keep_recent=get_compact_keep_recent(state),
        default_context_window=DEFAULT_COMPACT_CONTEXT_WINDOW,
        default_threshold_pct=DEFAULT_COMPACT_THRESHOLD_PCT,
        default_keep_recent=DEFAULT_COMPACT_KEEP_RECENT,
    )


@router.put("/system-settings/compact-config", response_model=CompactConfigOut)
def put_compact_config(
    payload: CompactConfigUpdateRequest,
    _admin: AdminGate,
) -> CompactConfigOut:
    """Persist a new compact-config triple."""
    state = _state_dir()
    state_set(state, COMPACT_CONTEXT_WINDOW_KEY, str(payload.context_window))
    state_set(state, COMPACT_THRESHOLD_PCT_KEY, str(payload.threshold_pct))
    state_set(state, COMPACT_KEEP_RECENT_KEY, str(payload.keep_recent))
    logger.info(
        "compact-config set: window=%d threshold=%d%% keep=%d",
        payload.context_window, payload.threshold_pct, payload.keep_recent,
    )
    return CompactConfigOut(
        context_window=payload.context_window,
        threshold_pct=payload.threshold_pct,
        keep_recent=payload.keep_recent,
        default_context_window=DEFAULT_COMPACT_CONTEXT_WINDOW,
        default_threshold_pct=DEFAULT_COMPACT_THRESHOLD_PCT,
        default_keep_recent=DEFAULT_COMPACT_KEEP_RECENT,
    )
