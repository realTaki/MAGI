"""System-level runtime config — neutral read helpers.

This module is the **package-neutral** home for KV-backed runtime
settings that ``magi.agent``, ``magi.tools`` and ``magi.proactive``
read on every chat turn. They are *not* HTTP handlers — those live
in :mod:`magi.channels.webui.api.system_settings` and wrap these
helpers with FastAPI dependencies.

Why this lives in :mod:`magi.db` (not under ``magi.channels.webui``):

  The runtime config is read by the agent loop, the tool loop and the
  scheduled-task runner — none of which should reach back into the
  channels layer to import a settings helper. Pulling these read
  helpers into a neutral module closes the
  ``agent → channels.webui.api.*`` reverse-import cycle that design
  §18 explicitly forbids.

The KV row ownership stays in :mod:`magi.db.models_setting.Setting`;
the table is unchanged — only the helper module moves.

Settings owned here:

  - ``system.timezone`` (read by :func:`get_system_timezone`)
  - ``system.tool_max_iterations`` (read by :func:`get_tool_max_iterations`)
  - ``system.compact_context_window`` / ``compact_threshold_pct`` /
    ``compact_keep_recent`` (read by the agent-loop compaction step)
  - ``system.show_daily_note`` / ``show_daily_note_prompt`` (read by
    the system-prompt builder)

The constant string keys (``SYSTEM_TZ_KEY`` etc.) are re-exported
from :mod:`magi.channels.webui.api.system_settings` so writes via
the WebUI API and reads via this module agree on the same row.
"""

from __future__ import annotations

import logging
import os
import zoneinfo
from typing import TYPE_CHECKING

from tzlocal import get_localzone

from magi.db.engine import require_state_dir
from magi.db.settings import state_get, state_set

if TYPE_CHECKING:
    pass

logger = logging.getLogger("magi.db.runtime_settings")

# ────────────────────────────────────────────────────────────────── #
# Settings keys — owned by system_settings.py for back-compat;
# re-exported here so writes via webui API and reads via this module
# share the same row.
# ────────────────────────────────────────────────────────────────── #

from magi.channels.webui.api.system_settings import (  # noqa: E402
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
)


# ────────────────────────────────────────────────────────────────── #
# Timezone
# ────────────────────────────────────────────────────────────────── #


def system_default_timezone() -> str:
    """Resolve the timezone used when ``system.timezone`` hasn't been set.

    Public version (no leading underscore) — callers outside this
    module are legitimate users of the fallback (e.g. the
    scheduled-task runner reading the system tz).

    Resolution order:

      1. ``TZ`` environment variable (set by the deployer in the
         k8s manifest / ConfigMap).
      2. :func:`get_localzone` (reads ``/etc/localtime``).
      3. ``Etc/UTC`` as a well-formed IANA fallback.
    """
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            zoneinfo.ZoneInfo(tz_env)  # validate
            return tz_env
        except Exception:
            logger.debug("TZ env %r is not a valid IANA name", tz_env)
    try:
        return get_localzone().key
    except Exception:
        pass
    return "Etc/UTC"


def get_system_timezone(state_dir: str) -> str:
    """Return the configured timezone name (e.g. ``"UTC"``).

    Falls back to :func:`system_default_timezone` (server's local
    timezone) when the stored value is empty / invalid. Validation
    runs through :class:`zoneinfo.ZoneInfo` so a hand-edited garbage
    value can't crash the aggregation endpoint.
    """
    raw = state_get(state_dir, SYSTEM_TZ_KEY)
    if not raw:
        return system_default_timezone()
    try:
        zoneinfo.ZoneInfo(raw)
    except Exception:
        logger.warning(
            "system.timezone stored value %r is not a valid IANA tz; "
            "falling back to %s",
            raw, system_default_timezone(),
        )
        return system_default_timezone()
    return raw


def set_system_timezone(state_dir: str, tz: str) -> None:
    """Persist a new timezone.

    Validates via :class:`zoneinfo.ZoneInfo`; raises
    :class:`zoneinfo.ZoneInfoNotFoundError` on an unknown name.
    Caller (the API handler) maps that to a 400.
    """
    zoneinfo.ZoneInfo(tz)  # raises on invalid
    state_set(state_dir, SYSTEM_TZ_KEY, tz)


# ────────────────────────────────────────────────────────────────── #
# Tool-loop max iterations
# ────────────────────────────────────────────────────────────────── #


def get_tool_max_iterations(state_dir: str) -> int:
    """Return the configured max tool iterations.

    Falls back to :data:`DEFAULT_TOOL_MAX_ITERATIONS` (10) when the
    stored value is missing / non-numeric / outside ``[MIN, MAX]``.
    The bounds-clamp is defensive — a hand-edited 0 would mean
    "agent can never call any tool", which would silently break
    the LLM's tool-use loop.
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


# ────────────────────────────────────────────────────────────────── #
# Compaction (D.17)
# ────────────────────────────────────────────────────────────────── #


def _clamp_int(raw, *, default, lo, hi, label):
    """Parse an int from a meta-key string and clamp to ``[lo, hi]``."""
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


def get_compact_context_window(state_dir: str) -> int:
    return _clamp_int(
        state_get(state_dir, COMPACT_CONTEXT_WINDOW_KEY),
        default=DEFAULT_COMPACT_CONTEXT_WINDOW,
        lo=MIN_COMPACT_CONTEXT_WINDOW,
        hi=MAX_COMPACT_CONTEXT_WINDOW,
        label="context_window",
    )


def get_compact_threshold_pct(state_dir: str) -> int:
    return _clamp_int(
        state_get(state_dir, COMPACT_THRESHOLD_PCT_KEY),
        default=DEFAULT_COMPACT_THRESHOLD_PCT,
        lo=MIN_COMPACT_THRESHOLD_PCT,
        hi=MAX_COMPACT_THRESHOLD_PCT,
        label="threshold_pct",
    )


def get_compact_keep_recent(state_dir: str) -> int:
    return _clamp_int(
        state_get(state_dir, COMPACT_KEEP_RECENT_KEY),
        default=DEFAULT_COMPACT_KEEP_RECENT,
        lo=MIN_COMPACT_KEEP_RECENT,
        hi=MAX_COMPACT_KEEP_RECENT,
        label="keep_recent",
    )


# ────────────────────────────────────────────────────────────────── #
# Daily-note toggle
# ────────────────────────────────────────────────────────────────── #


def _read_bool_setting(state_dir: str, key: str, *, default: bool) -> bool:
    """Parse a bool from a meta-key string.

    Accepts ``"true"`` / ``"1"`` (case-insensitive) as True; everything
    else (including missing and empty) falls back to ``default``. Same
    shape as the :func:`system_default_timezone` parser — the rest
    of the codebase persists "true" / "false" as the literal string.
    """
    raw = state_get(state_dir, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def get_show_daily_note(state_dir: str) -> bool:
    """Whether today's daily note body is rendered into the system prompt."""
    return _read_bool_setting(state_dir, SHOW_DAILY_NOTE_KEY, default=True)


def get_show_daily_note_prompt(state_dir: str) -> bool:
    """Whether the capture-rules text folds into the daily-note block header."""
    return _read_bool_setting(state_dir, SHOW_DAILY_NOTE_PROMPT_KEY, default=False)


__all__ = [
    # timezone
    "system_default_timezone",
    "get_system_timezone",
    "set_system_timezone",
    # tool loop
    "get_tool_max_iterations",
    # compaction
    "get_compact_context_window",
    "get_compact_threshold_pct",
    "get_compact_keep_recent",
    # daily note
    "get_show_daily_note",
    "get_show_daily_note_prompt",
]