"""System-level config: timezone + tool-iterations + compact + daily-note.

Per-MAGI-node settings (Adam has its own, every EVE has its own).
Stored in the same ``settings`` meta-key table that already holds
``tg.read_reaction_emoji`` and the bot token, so it inherits the
existing ``state_get`` / ``state_set`` / WAL concurrency story.

This module owns only the **HTTP surface** — the FastAPI router,
Pydantic request/response models, and the constants for the KV
keys. The read helpers themselves live in
:mod:`magi.db.runtime_settings` so the agent loop and other
non-channel code can read them without crossing back into the
``channels.webui`` tree.

KV key ownership stays here so a webui API write and a runtime
read share the same row, with one source of truth.
"""

from __future__ import annotations

import logging
import zoneinfo
from typing import Annotated

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from magi.channels.webui.api.auth_gates import AdminGate
from magi.db.engine import require_state_dir
from magi.db.runtime_settings import (
    COMPACT_CONTEXT_WINDOW_KEY,
    COMPACT_KEEP_RECENT_KEY,
    COMPACT_THRESHOLD_PCT_KEY,
    DEFAULT_COMPACT_CONTEXT_WINDOW,
    DEFAULT_COMPACT_KEEP_RECENT,
    DEFAULT_COMPACT_THRESHOLD_PCT,
    DEFAULT_TOOL_MAX_ITERATIONS,
    MAX_COMPACT_CONTEXT_WINDOW,
    MAX_COMPACT_KEEP_RECENT,
    MAX_COMPACT_THRESHOLD_PCT,
    MAX_TOOL_MAX_ITERATIONS,
    MIN_COMPACT_CONTEXT_WINDOW,
    MIN_COMPACT_KEEP_RECENT,
    MIN_COMPACT_THRESHOLD_PCT,
    MIN_TOOL_MAX_ITERATIONS,
    SHOW_DAILY_NOTE_KEY,
    SHOW_DAILY_NOTE_PROMPT_KEY,
    SYSTEM_TZ_KEY,
    TOOL_MAX_ITERATIONS_KEY,
    get_compact_context_window as _get_compact_context_window,
    get_compact_keep_recent as _get_compact_keep_recent,
    get_compact_threshold_pct as _get_compact_threshold_pct,
    get_show_daily_note as _get_show_daily_note,
    get_show_daily_note_prompt as _get_show_daily_note_prompt,
    get_system_timezone as _get_system_timezone,
    get_tool_max_iterations as _get_tool_max_iterations,
    set_system_timezone as _set_system_timezone,
    system_default_timezone as _system_default_timezone,
)
from magi.db.settings import state_set

logger = logging.getLogger("magi.api.system_settings")

router = APIRouter(tags=["system-settings"])

# All KV key / bounds / default constants are owned by
# ``magi.db.runtime_settings`` and re-exported above so the webui
# API writes the same row the runtime reads. Keep that as the
# single source of truth.


def _state_dir() -> str:
    return require_state_dir()


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
def get_system_timezone_endpoint(_admin: AdminGate) -> TimezoneOut:
    return TimezoneOut(
        current=_get_system_timezone(_state_dir()),
        default=_system_default_timezone(),
        choices=sorted(zoneinfo.available_timezones()),
    )


@router.put("/system-settings/timezone", response_model=TimezoneOut)
def put_system_timezone(
    payload: TimezoneUpdateRequest,
    _admin: AdminGate,
) -> TimezoneOut:
    """Persist a new system timezone.

    Validates against the IANA tz database; an unknown name returns
    400 ``validation.unknown_timezone`` so the operator gets a clear
    hint instead of a silent fallback to UTC.
    """
    from magi.channels.webui.api.errors import MagiHTTPException

    tz = payload.timezone
    try:
        _set_system_timezone(_state_dir(), tz)
    except zoneinfo.ZoneInfoNotFoundError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.unknown_timezone",
            detail=f"timezone {tz!r} is not a valid IANA tz name",
        )
    # Drop the cached value in ``tasks._resolve_system_tz`` so the
    # next task read picks up the new value.
    from magi.channels.webui.api.tasks import _invalidate_system_tz_cache
    _invalidate_system_tz_cache()
    logger.info("system.timezone set to %r", tz)
    return TimezoneOut(
        current=tz,
        default=_system_default_timezone(),
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
def get_tool_max_iterations_endpoint(_admin: AdminGate) -> ToolMaxIterationsOut:
    return ToolMaxIterationsOut(
        current=_get_tool_max_iterations(_state_dir()),
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
    """Persist a new max tool iterations value."""
    state_set(_state_dir(), TOOL_MAX_ITERATIONS_KEY, str(payload.value))
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
        context_window=_get_compact_context_window(state),
        threshold_pct=_get_compact_threshold_pct(state),
        keep_recent=_get_compact_keep_recent(state),
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


# ────────────────────────────────────────────────────────────────── #
# Daily-note toggle
# ────────────────────────────────────────────────────────────────── #


# Re-export the read helpers so any code that imported them from this
# module keeps working without changes (the implementation moved but
# the public surface is identical).
def get_show_daily_note(state_dir: str) -> bool:
    return _get_show_daily_note(state_dir)


def get_show_daily_note_prompt(state_dir: str) -> bool:
    return _get_show_daily_note_prompt(state_dir)