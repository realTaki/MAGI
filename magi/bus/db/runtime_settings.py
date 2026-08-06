"""System-level runtime config — neutral read helpers.

This module is the **package-neutral** home for KV-backed runtime
settings that ``magi.agent``, ``magi.tools`` and ``magi.proactive``
read on every chat turn. They are *not* HTTP handlers — those live
in :mod:`magi.channels.api.system_settings` and wrap these
helpers with FastAPI dependencies.

Why this lives in :mod:`magi.db` (not under ``magi.channels.api``):

  The runtime config is read by the agent loop, the tool loop and the
  scheduled-task runner — none of which should reach back into the
  channels layer to import a settings helper. Pulling these read
  helpers into a neutral module closes the
  ``agent → channels.webui.api.*`` reverse-import cycle that design
  §18 explicitly forbids.

The KV row ownership stays in :mod:`magi.bus.db.models.local.setting.Setting`;
the table is unchanged — only the helper module moves.

Settings owned here:

  - ``system.timezone`` (read by :func:`get_system_timezone`)
  - ``system.tool_max_iterations`` (read by :func:`get_tool_max_iterations`)
  - ``system.compact_context_window`` / ``compact_threshold_pct`` /
    ``compact_keep_recent`` (read by the agent-loop compaction step)
  - ``system.show_daily_note`` / ``show_daily_note_prompt`` (read by
    the system-prompt builder)

The constant string keys (``SYSTEM_TZ_KEY`` etc.) are re-exported
from :mod:`magi.channels.api.system_settings` so writes via
the WebUI API and reads via this module agree on the same row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import zoneinfo
from pathlib import Path
from typing import TYPE_CHECKING

from tzlocal import get_localzone

from magi.bus.db.engine import require_state_dir
from magi.bus.db.settings import state_get, state_set

if TYPE_CHECKING:
    pass

logger = logging.getLogger("magi.db.runtime_settings")

# ────────────────────────────────────────────────────────────────── #
# Settings keys + bounds + defaults — single source of truth.
#
# The HTTP layer (``magi.channels.api.system_settings``)
# imports these constants back from here so a Save via the
# dashboard writes the same row a runtime read sees. The reverse
# direction would be a circular import — keep them here.
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
    # TOML provider config (merged from bus/runtime_settings.py)
    "RuntimeSettings",
    "RUNTIME_SETTINGS_FILENAME",
    "load_runtime_settings",
    "save_runtime_settings",
]


# ────────────────────────────────────────────────────────────────── #
# Per-MAGI provider / API key / model — atomic TOML I/O
#
# Merged from the old ``magi.bus.runtime_settings`` module.  Provider
# credentials used to live on the shared ``magic`` row inside the
# direct MAGIS SQLite/Postgres database.  Every runtime in the same
# MAGIS could read the same secret, and the column never carried a
# model field.  The 2026-08 creation-flow refactor moves the three
# values into a small TOML file that lives next to each runtime's
# workspace:
#
#     <workspace>/runtime_settings.toml
#
# This module is the single read / write surface.  Both the agent
# loop's provider factory and the WebUI's runtime-self endpoint
# funnel through here so the schema stays in one place.
#
# Atomic write:
#     * write to ``<file>.tmp`` then ``os.replace()`` so a crash
#       mid-write never leaves a half-written config the next read
#       could load.
#     * parse errors return the empty defaults — a single bad
#       character in the file shouldn't brick the runtime.  The
#       misconfiguration surfaces the next time the operator tries
#       to start the runtime.
#
# File path resolution:
#     * K8s profile: ``$MAGI_WORKSPACE_DIR/runtime_settings.toml``
#     * CLI profile: ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<slug>/workspace/runtime_settings.toml``
#     * The lookup is delegated to :func:`magi.launcher.paths.workspace_dir`
#       so a single env var is enough for either profile.
#
# In-process concurrency:
#     * a process-wide :class:`asyncio.Lock` keyed by the resolved
#       path guards write/read pairs so two simultaneous PATCH calls
#       don't interleave.
#
# Side-effect:
#     * On successful save, a ``provider.config_changed`` control
#       job is enqueued so the ProvidersWorker rebuilds its cached
#       SDK client.  The publish is best-effort — if the bus store
#       isn't available yet (bootstrap / tests) the worker will
#       lazy-rebuild on its next claim.
# ────────────────────────────────────────────────────────────────── #

from dataclasses import asdict, dataclass
from typing import Any


RUNTIME_SETTINGS_FILENAME = "runtime_settings.toml"


@dataclass(frozen=True)
class RuntimeSettings:
    """Per-MAGI provider / API key / model triple.

    ``None`` for any field means "not configured"; the provider factory
    treats a missing API key as "MAGI cannot call any LLM yet".  The
    file format is a small JSON document for forward-compatibility —
    switching to TOML would just trade one parser for another.
    """

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.provider) and bool(self.api_key)


def _runtime_settings_path() -> Path:
    """Resolve the active MAGI's runtime-settings file path."""
    from magi.launcher.paths import workspace_dir

    return workspace_dir() / RUNTIME_SETTINGS_FILENAME


# Per-path write lock — concurrent PATCH calls on the same file would
# otherwise race the read-modify-write cycle below.  ``Path`` is
# hashable, so we can key by the resolved file path.
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
    """Read the runtime-settings file.

    Returns :class:`RuntimeSettings` (all ``None``) when the file is
    missing or unreadable; a corrupt file is logged and treated as
    unconfigured rather than crashing the boot.
    """
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
    """Atomically write ``settings`` to the per-MAGI settings file.

    Sync helper used by the runtime endpoint and the bootstrap.  The
    write is atomic (``tempfile + os.replace``) so a crash mid-write
    leaves the previous valid file in place.  Creates parent
    directories on demand.
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

    The ``magi.bus.bootstrap`` import is deferred to avoid the
    SQLAlchemy ``Mapped`` double-registration when the bus store has
    not been built yet.
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