"""new_bus runtime settings — TOML provider config + system KV helpers.

Per-MAGI provider / API key / model triple, stored in a small TOML
file at ``<workspace>/runtime_settings.toml``.  Plus the system-level
KV helpers (timezone, tool_max_iterations, compaction, daily-note
toggle) used by the agent loop.

This module is new_bus's **standalone copy** of the system runtime
settings surface.  The old bus keeps its own copy at
``magi/bus/db/runtime_settings.py`` — both files live in parallel
so neither side depends on the other.

Public surface
=============

- :func:`load_runtime_settings` / :func:`save_runtime_settings` /
  :class:`RuntimeSettings` / :data:`RUNTIME_SETTINGS_FILENAME` —
  the TOML provider-config file I/O
- :func:`get_system_timezone` / :func:`set_system_timezone` /
  :func:`get_tool_max_iterations` /
  :func:`get_compact_context_window` /
  :func:`get_compact_threshold_pct` /
  :func:`get_compact_keep_recent` /
  :func:`get_show_daily_note` / :func:`get_show_daily_note_prompt` —
  system KV helpers (read from the ``settings`` table)
- Constants: :data:`SYSTEM_TZ_KEY` etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("magi.new_bus.runtime_settings")


# ───────────────────────────────────────────────────────────────────── #
# TOML provider config (atomic file I/O)
# ───────────────────────────────────────────────────────────────────── #


RUNTIME_SETTINGS_FILENAME = "runtime_settings.toml"


@dataclass(frozen=True)
class RuntimeSettings:
    """Per-MAGI provider / API key / model triple.

    ``None`` for any field means "not configured"; the provider
    factory treats a missing API key as "MAGI cannot call any LLM
    yet".  The on-disk format is small JSON for forward-compat.
    """

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.provider) and bool(self.api_key)


def _runtime_settings_path() -> Path:
    from magi.launcher.paths import workspace_dir

    return workspace_dir() / RUNTIME_SETTINGS_FILENAME


# Per-path write lock — concurrent PATCH calls on the same file
# would otherwise race the read-modify-write cycle below.
_WRITE_LOCKS: dict[str, asyncio.Lock] = {}
_WRITE_LOCKS_GUARD = asyncio.Lock()


def _lock_for(path: Path) -> asyncio.Lock:
    """Return (creating if needed) the asyncio.Lock for ``path``."""
    key = str(path.resolve())
    cached = _WRITE_LOCKS.get(key)
    if cached is not None:
        return cached
    fut = asyncio.Lock()
    _WRITE_LOCKS[key] = fut
    return fut


def load_runtime_settings(*, path: Path | None = None) -> RuntimeSettings:
    """Read the runtime-settings file; return all-``None`` on missing/corrupt."""
    target = path or _runtime_settings_path()
    if not target.is_file():
        return RuntimeSettings()
    try:
        with target.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "runtime_settings: failed to read %s (%s); treating as unconfigured",
            target, exc,
        )
        return RuntimeSettings()
    if not isinstance(data, dict):
        return RuntimeSettings()
    return RuntimeSettings(
        provider=_str_or_none(data.get("provider")),
        api_key=_str_or_none(data.get("api_key")),
        model=_str_or_none(data.get("model")),
    )


def save_runtime_settings(
    settings: RuntimeSettings,
    *,
    path: Path | None = None,
) -> Path:
    """Atomically write ``settings`` to the per-MAGI TOML file.

    Sync helper used by the runtime endpoint and the bootstrap.
    The write is atomic (``tempfile + os.replace``).
    """
    target = path or _runtime_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        k: v for k, v in asdict(settings).items() if v is not None
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info(
        "runtime_settings: wrote provider=%r model=%r to %s",
        settings.provider, settings.model, target,
    )
    _publish_provider_config_changed(settings)
    return target


def _publish_provider_config_changed(settings: RuntimeSettings) -> None:
    """Best-effort insert of one ``provider.config_changed`` row.

    Looks up the bus store lazily to avoid an import cycle.  If the
    bus store is not yet available (bootstrap, tests) the worker
    will lazy-rebuild on the next claim.
    """
    try:
        from magi.bus.bootstrap import get_bus_store
        store = get_bus_store()
    except Exception:
        logger.debug(
            "runtime_settings: no bus store; provider worker will lazy-rebuild",
        )
        return
    try:
        from magi.bus.jobs.protocols.control_jobs import (
            PROVIDER_CONFIG_CHANGED,
        )
        store.enqueue_control_job(
            kind=PROVIDER_CONFIG_CHANGED,
            payload={
                "provider": settings.provider,
                "model": settings.model,
            },
        )
    except Exception:
        logger.exception(
            "runtime_settings: could not publish provider.config_changed",
        )


def _str_or_none(value: Any) -> str | None:
    """Coerce a JSON-decoded value to ``str`` or ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


# ───────────────────────────────────────────────────────────────────── #
# System-level KV helpers (read from ``settings`` table)
# ───────────────────────────────────────────────────────────────────── #


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


def system_default_timezone() -> str:
    """Resolve the timezone used when ``system.timezone`` hasn't been set."""
    import zoneinfo

    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            zoneinfo.ZoneInfo(tz_env)
            return tz_env
        except Exception:
            logger.debug("TZ env %r is not a valid IANA name", tz_env)
    try:
        from tzlocal import get_localzone
        return get_localzone().key
    except Exception:
        return "Etc/UTC"


def _state_get(key: str) -> str | None:
    """Read a setting from the ``settings`` table via the bus store.

    Falls back to a no-op if the bus store is unavailable.
    """
    try:
        from magi.bus.bootstrap import get_bus
        bus = get_bus()
    except Exception:
        return None
    return bus.settings.get(key)


def get_system_timezone() -> str:
    raw = _state_get(SYSTEM_TZ_KEY)
    if not raw:
        return system_default_timezone()
    import zoneinfo
    try:
        zoneinfo.ZoneInfo(raw)
    except Exception:
        return system_default_timezone()
    return raw


def set_system_timezone(tz: str) -> None:
    import zoneinfo
    zoneinfo.ZoneInfo(tz)
    from magi.bus.bootstrap import get_bus
    get_bus().settings.set(tz, SYSTEM_TZ_KEY)


def get_tool_max_iterations() -> int:
    raw = _state_get(TOOL_MAX_ITERATIONS_KEY)
    try:
        value = int(raw) if raw is not None else DEFAULT_TOOL_MAX_ITERATIONS
    except (TypeError, ValueError):
        return DEFAULT_TOOL_MAX_ITERATIONS
    if value < MIN_TOOL_MAX_ITERATIONS or value > MAX_TOOL_MAX_ITERATIONS:
        return max(MIN_TOOL_MAX_ITERATIONS, min(MAX_TOOL_MAX_ITERATIONS, value))
    return value


def get_compact_context_window() -> int:
    return _clamp_int(
        _state_get(COMPACT_CONTEXT_WINDOW_KEY),
        default=DEFAULT_COMPACT_CONTEXT_WINDOW,
        lo=MIN_COMPACT_CONTEXT_WINDOW,
        hi=MAX_COMPACT_CONTEXT_WINDOW,
        label="context_window",
    )


def get_compact_threshold_pct() -> int:
    return _clamp_int(
        _state_get(COMPACT_THRESHOLD_PCT_KEY),
        default=DEFAULT_COMPACT_THRESHOLD_PCT,
        lo=MIN_COMPACT_THRESHOLD_PCT,
        hi=MAX_COMPACT_THRESHOLD_PCT,
        label="threshold_pct",
    )


def get_compact_keep_recent() -> int:
    return _clamp_int(
        _state_get(COMPACT_KEEP_RECENT_KEY),
        default=DEFAULT_COMPACT_KEEP_RECENT,
        lo=MIN_COMPACT_KEEP_RECENT,
        hi=MAX_COMPACT_KEEP_RECENT,
        label="keep_recent",
    )


def _clamp_int(raw, *, default, lo, hi, label):
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    if v < lo or v > hi:
        return max(lo, min(hi, v))
    return v


def get_show_daily_note() -> bool:
    return _read_bool_setting(SHOW_DAILY_NOTE_KEY, default=True)


def get_show_daily_note_prompt() -> bool:
    return _read_bool_setting(SHOW_DAILY_NOTE_PROMPT_KEY, default=False)


def _read_bool_setting(key: str, *, default: bool) -> bool:
    raw = _state_get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


__all__ = [
    "RUNTIME_SETTINGS_FILENAME",
    "RuntimeSettings",
    "load_runtime_settings",
    "save_runtime_settings",
    # system KV
    "SYSTEM_TZ_KEY",
    "TOOL_MAX_ITERATIONS_KEY",
    "COMPACT_CONTEXT_WINDOW_KEY",
    "COMPACT_THRESHOLD_PCT_KEY",
    "COMPACT_KEEP_RECENT_KEY",
    "SHOW_DAILY_NOTE_KEY",
    "SHOW_DAILY_NOTE_PROMPT_KEY",
    "system_default_timezone",
    "get_system_timezone",
    "set_system_timezone",
    "get_tool_max_iterations",
    "get_compact_context_window",
    "get_compact_threshold_pct",
    "get_compact_keep_recent",
    "get_show_daily_note",
    "get_show_daily_note_prompt",
]
