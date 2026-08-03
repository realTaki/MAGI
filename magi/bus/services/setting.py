"""Bus service: setting (BUS-owned façade for local runtime settings).

The underlying KV lives in :mod:`magi.db.settings` and remains the
physical storage; the bus service is the only allowed access path for
non-bus code.
"""

from __future__ import annotations


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
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        from magi.db.runtime_settings import system_default_timezone
        raw = self.get("system.timezone")
        if raw:
            try:
                ZoneInfo(raw)
                return raw
            except ZoneInfoNotFoundError:
                pass
        return system_default_timezone()

    def compaction_policy(self) -> tuple[int, int, int]:
        """Return ``(context_window, threshold_percent, keep_recent)``."""
        from magi.db.runtime_settings import (
            get_compact_context_window,
            get_compact_keep_recent,
            get_compact_threshold_pct,
        )

        return (
            get_compact_context_window(self._state_dir),
            get_compact_threshold_pct(self._state_dir),
            get_compact_keep_recent(self._state_dir),
        )

    def show_daily_note(self) -> tuple[bool, bool]:
        from magi.db.runtime_settings import get_show_daily_note, get_show_daily_note_prompt

        return get_show_daily_note(self._state_dir), get_show_daily_note_prompt(self._state_dir)
