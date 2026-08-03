"""Bus service: setting (BUS-owned façade for local runtime settings).

The underlying KV lives in :mod:`magi.db.settings` and remains the
physical storage; the bus service is the only allowed access path for
non-bus code.
"""

from __future__ import annotations

import logging
import os
import zoneinfo

logger = logging.getLogger("magi.bus.services.setting")


# ────────────────────────────────────────────────────────────────── #
# Settings keys + bounds + defaults — single source of truth.
#
# Moved here from ``magi.db.runtime_settings`` so the HTTP layer
# (channels.webui) can read/write them via the bus without crossing
# back to the db layer (which the architecture test forbids).
# ────────────────────────────────────────────────────────────────── #

SYSTEM_TZ_KEY = "system.timezone"
TOOL_MAX_ITERATIONS_KEY = "system.tool_max_iterations"
COMPACT_CONTEXT_WINDOW_KEY = "system.compact_context_window"
COMPACT_THRESHOLD_PCT_KEY = "system.compact_threshold_pct"
COMPACT_KEEP_RECENT_KEY = "system.compact_keep_recent"
SHOW_DAILY_NOTE_KEY = "system.show_daily_note"
SHOW_DAILY_NOTE_PROMPT_KEY = "system.show_daily_note_prompt"

DEFAULT_TOOL_MAX_ITERATIONS = 10
MAX_TOOL_MAX_ITERATIONS = 50
MIN_TOOL_MAX_ITERATIONS = 1

DEFAULT_COMPACT_CONTEXT_WINDOW = 100000
DEFAULT_COMPACT_THRESHOLD_PCT = 80
DEFAULT_COMPACT_KEEP_RECENT = 20

MIN_COMPACT_CONTEXT_WINDOW = 16000
MAX_COMPACT_CONTEXT_WINDOW = 200000
MIN_COMPACT_THRESHOLD_PCT = 50
MAX_COMPACT_THRESHOLD_PCT = 95
MIN_COMPACT_KEEP_RECENT = 5
MAX_COMPACT_KEEP_RECENT = 100


def _system_default_timezone() -> str:
    """Resolve the timezone used when ``system.timezone`` hasn't been set."""
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            zoneinfo.ZoneInfo(tz_env)  # validate
            return tz_env
        except Exception:
            logger.debug("TZ env %r is not a valid IANA name", tz_env)
    try:
        from tzlocal import get_localzone
        return get_localzone().key
    except Exception:
        pass
    return "Etc/UTC"


class SettingsService:
    """Read / write runtime settings via the local settings KV."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def get(self, key: str) -> str | None:
        from magi.db.settings import state_get

        return state_get(self._state_dir, key)

    def set(self, key: str, value: str) -> None:
        from magi.db.settings import state_set

        return state_set(self._state_dir, key, value)

    def delete(self, key: str) -> None:
        """Delete one runtime setting through the BUS persistence boundary."""
        from magi.db.settings import state_delete

        state_delete(self._state_dir, key)

    @staticmethod
    def require_state_dir() -> str:
        """Return the runtime's configured state directory.

        This is a process-wide constant set by the runtime entry
        point; tools and workers that don't receive ``state_dir`` on
        their context fall back to this.
        """
        from magi.db.engine import require_state_dir
        return require_state_dir()

    def system_timezone(self) -> str:
        """Read the configured system timezone, with safe fallbacks.

        Mirrors the ``system_settings._system_default_timezone``
        helper that ``GET /api/system-settings/timezone`` returns.
        A stored-but-invalid IANA value falls back to the server-
        local default rather than failing.
        """
        raw = self.get(SYSTEM_TZ_KEY)
        if raw:
            try:
                zoneinfo.ZoneInfo(raw)
                return raw
            except zoneinfo.ZoneInfoNotFoundError:
                pass
        return _system_default_timezone()

    def system_default_timezone(self) -> str:
        """Return the server's local-default IANA name (no KV read)."""
        return _system_default_timezone()

    def set_system_timezone(self, tz: str) -> None:
        """Persist a new system timezone; raises ``ZoneInfoNotFoundError`` on invalid names."""
        zoneinfo.ZoneInfo(tz)  # raises on invalid
        self.set(SYSTEM_TZ_KEY, tz)

    def tool_max_iterations(self) -> int:
        """Return the configured max tool iterations, clamped to ``[MIN, MAX]``."""
        raw = self.get(TOOL_MAX_ITERATIONS_KEY)
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

    def compact_context_window(self) -> int:
        return self._clamp_int(
            self.get(COMPACT_CONTEXT_WINDOW_KEY),
            default=DEFAULT_COMPACT_CONTEXT_WINDOW,
            lo=MIN_COMPACT_CONTEXT_WINDOW,
            hi=MAX_COMPACT_CONTEXT_WINDOW,
            label="context_window",
        )

    def compact_threshold_pct(self) -> int:
        return self._clamp_int(
            self.get(COMPACT_THRESHOLD_PCT_KEY),
            default=DEFAULT_COMPACT_THRESHOLD_PCT,
            lo=MIN_COMPACT_THRESHOLD_PCT,
            hi=MAX_COMPACT_THRESHOLD_PCT,
            label="threshold_pct",
        )

    def compact_keep_recent(self) -> int:
        return self._clamp_int(
            self.get(COMPACT_KEEP_RECENT_KEY),
            default=DEFAULT_COMPACT_KEEP_RECENT,
            lo=MIN_COMPACT_KEEP_RECENT,
            hi=MAX_COMPACT_KEEP_RECENT,
            label="keep_recent",
        )

    def show_daily_note(self) -> bool:
        return self._read_bool(SHOW_DAILY_NOTE_KEY, default=True)

    def show_daily_note_prompt(self) -> bool:
        return self._read_bool(SHOW_DAILY_NOTE_PROMPT_KEY, default=False)

    @staticmethod
    def _clamp_int(raw, *, default, lo, hi, label):
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

    @staticmethod
    def _read_bool(key_value: str, *, default: bool) -> bool:
        if key_value is None or key_value == "":
            return default
        return key_value.strip().lower() in {"true", "1", "yes", "on"}