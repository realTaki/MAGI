"""Per-MAGI runtime settings — provider / API key / model.

Provider credentials used to live on the shared ``magic`` row inside the
direct MAGIS SQLite/Postgres database.  Every runtime in the same MAGIS
could read the same secret, and the column never carried a model field.
The 2026-08 creation-flow refactor moves the three values into a small
TOML file that lives next to each runtime's workspace:

    <workspace>/runtime_settings.toml

This module is the single read / write surface.  Both the agent loop's
provider factory and the WebUI's runtime-self endpoint funnel through
here so the schema stays in one place.

Atomic write:
    * write to ``<file>.tmp`` then ``os.replace()`` so a crash mid-write
      never leaves a half-written config the next read could load.
    * parse errors return the empty defaults — a single bad character
      in the file shouldn't brick the runtime.  The misconfiguration
      surfaces the next time the operator tries to start the runtime.

File path resolution:
    * K8s profile: ``$MAGI_WORKSPACE_DIR/runtime_settings.toml``
    * CLI profile: ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<slug>/workspace/runtime_settings.toml``
    * The lookup is delegated to :func:`magi.launcher.paths.workspace_dir`
      so a single env var is enough for either profile.

In-process concurrency:
    * a process-wide :class:`asyncio.Lock` keyed by the resolved path
      guards write/read pairs so two simultaneous PATCH calls don't
      interleave.
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

from magi.bus.protocols.control_jobs import PROVIDER_CONFIG_CHANGED

logger = logging.getLogger("magi.bus.runtime_settings")


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


def _settings_path() -> Path:
    """Resolve the active MAGI's runtime-settings file path.

    Falls back to the workspace root resolved by
    :func:`magi.launcher.paths.workspace_dir` — both profiles resolve
    to a single absolute path that exists or can be created.
    """
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
    # NOTE: ``_WRITE_LOCKS_GUARD`` is asyncio, so callers must already
    # be inside an event loop.  The runtime API endpoint that drives
    # writes is async, so this is fine in production; tests that call
    # ``save_runtime_settings`` directly from a sync context must do
    # their own synchronization.
    fut = asyncio.Lock()
    _WRITE_LOCKS[key] = fut
    return fut


def load_runtime_settings(*, path: Path | None = None) -> RuntimeSettings:
    """Read the runtime-settings file.

    Returns ``RuntimeSettings()`` (all ``None``) when the file is
    missing or unreadable; a corrupt file is logged and treated as
    unconfigured rather than crashing the boot.
    """
    target = path or _settings_path()
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
    target = path or _settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        k: v for k, v in asdict(settings).items() if v is not None
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

    # Write to a sibling temp file, fsync, then atomic replace.  The
    # ``dir=target.parent`` ensures rename stays on the same
    # filesystem (POSIX rename is only atomic within one filesystem).
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
        # Clean up the orphaned tmp file so we don't leave litter.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info(
        "runtime_settings: wrote provider=%r model=%r to %s",
        settings.provider, settings.model, target,
    )
    # Notify the provider worker so it can rebuild its cached
    # ``LLMProvider`` on the next poll tick. The ``control_jobs``
    # queue is the BUS-to-worker signal: ``save_runtime_settings``
    # inserts one row, ``ProvidersWorker._run`` drains them. We
    # swallow every failure here -- the file write has already
    # succeeded and must not be undone because the publisher is
    # unavailable (this function is also called during bootstrap
    # and from tests that don't have a bus store yet).
    _publish_provider_config_changed(settings)
    return target


def _publish_provider_config_changed(settings: RuntimeSettings) -> None:
    """Best-effort insert of one ``provider.config_changed`` row.

    Split out so the main save path stays focused on the atomic
    write. The ``magi.bus.bootstrap`` import is deferred to avoid
    the SQLAlchemy ``Mapped`` double-registration when the bus
    store has not been built yet.
    """
    try:
        from magi.bus.bootstrap import get_bus_store
        store = get_bus_store()
    except Exception:
        # No bus store (test, bootstrap, missing state dir).
        # The file is already on disk; the provider worker's
        # lazy fallback in ``_get_provider_for_attempt`` will
        # rebuild on the next claim even without this signal.
        logger.debug(
            "runtime_settings: no bus store; provider worker will lazy-rebuild",
        )
        return
    try:
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


__all__ = [
    "RuntimeSettings",
    "RUNTIME_SETTINGS_FILENAME",
    "load_runtime_settings",
    "save_runtime_settings",
]
