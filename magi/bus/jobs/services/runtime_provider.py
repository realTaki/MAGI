"""Bus service: runtime provider config (per-MAGI TOML settings).

Wraps the file I/O in :mod:`magi.bus.db.runtime_settings` so external
modules (``magi.channels.api.runtime_provider``, …) read / write the
per-MAGI ``provider / api_key / model`` triple through the bus
façade rather than reaching into the ``db`` layer directly.

The :class:`RuntimeSettings` dataclass is re-exported so callers
that need the typed shape (e.g. for Pydantic adapters) can import
it via the bus surface.
"""

from __future__ import annotations

import logging

from magi.bus.db.runtime_settings import (
    RUNTIME_SETTINGS_FILENAME,
    RuntimeSettings,
    load_runtime_settings,
    save_runtime_settings,
)

logger = logging.getLogger("magi.bus.jobs.services.runtime_provider")


class RuntimeProviderService:
    """BUS façade over the per-MAGI runtime settings file.

    Today this is a thin wrapper — the underlying state lives in a
    single TOML file at ``$workspace/runtime_settings.toml``; the
    bus is the only allowed access path for non-bus code.
    """

    def __init__(self) -> None:
        pass

    def get(self) -> RuntimeSettings:
        """Return the current per-MAGI provider/api_key/model triple.

        Returns an all-``None`` :class:`RuntimeSettings` when the
        file is missing or unparseable.
        """
        return load_runtime_settings()

    def save(self, settings: RuntimeSettings) -> None:
        """Atomically write the per-MAGI settings file.

        Side effect: enqueues a ``provider.config_changed`` control
        job on the bus store so the ProvidersWorker rebuilds its
        cached SDK client on the next poll.
        """
        save_runtime_settings(settings)


__all__ = [
    "RUNTIME_SETTINGS_FILENAME",
    "RuntimeSettings",
    "RuntimeProviderService",
]
