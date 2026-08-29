"""System-level config: timezone + tool-iterations + compact.

Per-MAGI-node settings (ADAM has its own, every EVA has its own).
Stored in the same ``settings`` meta-key table that already holds
``tg.read_reaction_emoji`` and the bot token, so it inherits the
existing ``state_get`` / ``state_set`` / WAL concurrency story.

This module owns only the **HTTP surface** — the FastAPI router,
Pydantic request/response models, and the constants for the KV
keys.  Reads and writes go through bus Book API
so the API layer never crosses the channels → db boundary.
"""

from __future__ import annotations

import logging
import zoneinfo

from fastapi import APIRouter
from pydantic import BaseModel, Field

from channels.api.auth_gates import AdminGate
from channels.api.dependencies import BusDep

logger = logging.getLogger("magi.api.system_settings")

router = APIRouter(tags=["system-settings"])

SYSTEM_TZ_KEY = "system.timezone"
TOOL_MAX_ITERATIONS_KEY = "system.tool_max_iterations"
COMPACT_CONTEXT_WINDOW_KEY = "system.compact_context_window"
COMPACT_THRESHOLD_PCT_KEY = "system.compact_threshold_pct"
COMPACT_KEEP_RECENT_KEY = "system.compact_keep_recent"

DEFAULT_TOOL_MAX_ITERATIONS = 10
MIN_TOOL_MAX_ITERATIONS = 1
MAX_TOOL_MAX_ITERATIONS = 50
DEFAULT_COMPACT_CONTEXT_WINDOW = 100_000
DEFAULT_COMPACT_THRESHOLD_PCT = 80
DEFAULT_COMPACT_KEEP_RECENT = 20
MIN_COMPACT_CONTEXT_WINDOW = 16_000
MAX_COMPACT_CONTEXT_WINDOW = 200_000
MIN_COMPACT_THRESHOLD_PCT = 50
MAX_COMPACT_THRESHOLD_PCT = 95
MIN_COMPACT_KEEP_RECENT = 5
MAX_COMPACT_KEEP_RECENT = 100


def _settings(bus):
    """Return the bus settings service for the active state dir."""
    return bus.settings_book


def _default_timezone() -> str:
    try:
        return str(zoneinfo.ZoneInfo("localtime").key)
    except Exception:
        return "Etc/UTC"


def _read_int_setting(bus, *, key: str, default: int, minimum: int, maximum: int) -> int:
    raw = _settings(bus).get_value(key=key)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


# ────────────────────────────────────────────────────────────────── #
# Timezone
# ────────────────────────────────────────────────────────────────── #


class TimezoneOut(BaseModel):
    """``GET /api/system-settings/timezone`` response."""

    current: str
    default: str
    choices: list[str]


class TimezoneUpdateRequest(BaseModel):
    """``PUT /api/system-settings/timezone`` body."""

    timezone: str = Field(min_length=1, max_length=64)


@router.get("/system-settings/timezone", response_model=TimezoneOut)
def get_system_timezone_endpoint(_admin: AdminGate, bus: BusDep) -> TimezoneOut:
    svc = _settings(bus)
    default = _default_timezone()
    current = svc.get_value(key=SYSTEM_TZ_KEY) or default
    try:
        zoneinfo.ZoneInfo(current)
    except zoneinfo.ZoneInfoNotFoundError:
        current = default
    return TimezoneOut(
        current=current,
        default=default,
        choices=sorted(zoneinfo.available_timezones()),
    )


@router.put("/system-settings/timezone", response_model=TimezoneOut)
def put_system_timezone(
    payload: TimezoneUpdateRequest,
    _admin: AdminGate,
    bus: BusDep,
) -> TimezoneOut:
    """Persist a new system timezone.

    Validates against the IANA tz database; an unknown name returns
    400 ``validation.unknown_timezone`` so the operator gets a clear
    hint instead of a silent fallback to UTC.
    """
    from channels.api.errors import MagiHTTPException

    tz = payload.timezone
    svc = _settings(bus)
    try:
        zoneinfo.ZoneInfo(tz)
    except zoneinfo.ZoneInfoNotFoundError:
        raise MagiHTTPException(  # noqa: B904
            status_code=400,
            code="validation.unknown_timezone",
            detail=f"timezone {tz!r} is not a valid IANA tz name",
        )
    # No cache invalidation needed: every tz consumer reads
    # ``bus.settings_book.get_value("system.timezone")`` directly, so the
    # next read picks up the new value with no helper to call.
    logger.info("system.timezone set to %r", tz)
    svc.set(key=SYSTEM_TZ_KEY, value=tz)
    return TimezoneOut(
        current=tz,
        default=_default_timezone(),
        choices=sorted(zoneinfo.available_timezones()),
    )


# ────────────────────────────────────────────────────────────────── #
# Tool-loop max iterations (D.16)
# ────────────────────────────────────────────────────────────────── #


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
def get_tool_max_iterations_endpoint(_admin: AdminGate, bus: BusDep) -> ToolMaxIterationsOut:
    return ToolMaxIterationsOut(
        current=_read_int_setting(
            bus,
            key=TOOL_MAX_ITERATIONS_KEY,
            default=DEFAULT_TOOL_MAX_ITERATIONS,
            minimum=MIN_TOOL_MAX_ITERATIONS,
            maximum=MAX_TOOL_MAX_ITERATIONS,
        ),
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
    bus: BusDep,
) -> ToolMaxIterationsOut:
    """Persist a new max tool iterations value."""
    svc = _settings(bus)
    svc.set(key=TOOL_MAX_ITERATIONS_KEY, value=str(payload.value))
    logger.info("system.tool_max_iterations set to %d", payload.value)
    return ToolMaxIterationsOut(
        current=payload.value,
        default=DEFAULT_TOOL_MAX_ITERATIONS,
        min=MIN_TOOL_MAX_ITERATIONS,
        max=MAX_TOOL_MAX_ITERATIONS,
    )


# ────────────────────────────────────────────────────────────────── #
# Compaction (D.17)
# ────────────────────────────────────────────────────────────────── #


class CompactConfigOut(BaseModel):
    context_window: int
    threshold_pct: int
    keep_recent: int
    default_context_window: int
    default_threshold_pct: int
    default_keep_recent: int


class CompactConfigUpdateRequest(BaseModel):
    context_window: int = Field(ge=MIN_COMPACT_CONTEXT_WINDOW, le=MAX_COMPACT_CONTEXT_WINDOW)
    threshold_pct: int = Field(ge=MIN_COMPACT_THRESHOLD_PCT, le=MAX_COMPACT_THRESHOLD_PCT)
    keep_recent: int = Field(ge=MIN_COMPACT_KEEP_RECENT, le=MAX_COMPACT_KEEP_RECENT)


@router.get("/system-settings/compact-config", response_model=CompactConfigOut)
def get_compact_config(_admin: AdminGate, bus: BusDep) -> CompactConfigOut:
    return CompactConfigOut(
        context_window=_read_int_setting(
            bus,
            key=COMPACT_CONTEXT_WINDOW_KEY,
            default=DEFAULT_COMPACT_CONTEXT_WINDOW,
            minimum=MIN_COMPACT_CONTEXT_WINDOW,
            maximum=MAX_COMPACT_CONTEXT_WINDOW,
        ),
        threshold_pct=_read_int_setting(
            bus,
            key=COMPACT_THRESHOLD_PCT_KEY,
            default=DEFAULT_COMPACT_THRESHOLD_PCT,
            minimum=MIN_COMPACT_THRESHOLD_PCT,
            maximum=MAX_COMPACT_THRESHOLD_PCT,
        ),
        keep_recent=_read_int_setting(
            bus,
            key=COMPACT_KEEP_RECENT_KEY,
            default=DEFAULT_COMPACT_KEEP_RECENT,
            minimum=MIN_COMPACT_KEEP_RECENT,
            maximum=MAX_COMPACT_KEEP_RECENT,
        ),
        default_context_window=DEFAULT_COMPACT_CONTEXT_WINDOW,
        default_threshold_pct=DEFAULT_COMPACT_THRESHOLD_PCT,
        default_keep_recent=DEFAULT_COMPACT_KEEP_RECENT,
    )


@router.put("/system-settings/compact-config", response_model=CompactConfigOut)
def put_compact_config(
    payload: CompactConfigUpdateRequest,
    _admin: AdminGate,
    bus: BusDep,
) -> CompactConfigOut:
    """Persist a new compact-config triple."""
    svc = _settings(bus)
    svc.set(key=COMPACT_CONTEXT_WINDOW_KEY, value=str(payload.context_window))
    svc.set(key=COMPACT_THRESHOLD_PCT_KEY, value=str(payload.threshold_pct))
    svc.set(key=COMPACT_KEEP_RECENT_KEY, value=str(payload.keep_recent))
    logger.info(
        "compact-config set: window=%d threshold=%d%% keep=%d",
        payload.context_window,
        payload.threshold_pct,
        payload.keep_recent,
    )
    return CompactConfigOut(
        context_window=payload.context_window,
        threshold_pct=payload.threshold_pct,
        keep_recent=payload.keep_recent,
        default_context_window=DEFAULT_COMPACT_CONTEXT_WINDOW,
        default_threshold_pct=DEFAULT_COMPACT_THRESHOLD_PCT,
        default_keep_recent=DEFAULT_COMPACT_KEEP_RECENT,
    )
