"""Unified MAGI runtime composition (plan §14).

The :func:`run_magi` function is the single composition root for one
MAGI process. It:

1. Bootstraps identity via :func:`magi.startup.bootstrap.bootstrap_magi`.
2. Builds one :class:`~magi.bus.Bus` facade.
3. Brings up durable workers (provider / agent / tool / delivery).
4. Brings up channels (currently: telegram).
5. Serves the private runtime HTTP API on a fixed internal port.

It does **not**:

- Spawn subprocesses (use :mod:`magi.startup.local`).
- Create Kubernetes resources (use :mod:`magi.startup.kubernetes`).
- Manage the WebUI (use :mod:`magi.startup.webui`).
- Read or mutate environment variables at runtime.
- Allow runtime-side port / host configuration (plan §21).

Per plan §21, ``reload`` is mode-aware:

- Explicit ``MAGI_DEV_RELOAD=1`` → always on
- Explicit ``MAGI_DEV_RELOAD=0`` → always off
- Default (unset):
  - CLI / local mode (no ``KUBERNETES_SERVICE_HOST``) → **on**
  - K8s production (``KUBERNETES_SERVICE_HOST`` + ``MAGI_ENV=production``) → **off**
  - K8s dev (``KUBERNETES_SERVICE_HOST`` without ``MAGI_ENV=production``) → **on**

See :func:`_reload_enabled` for the implementation.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from magi.startup.config import (
    DEFAULT_LOG_LEVEL,
    RUNTIME_HOST,
    RUNTIME_PORT,
    StartupConfig,
    StartupContext,
)
from magi.startup.paths import resolve_private_database_url
from magi.startup.spec import load_runtime_spec

logger = logging.getLogger("magi.startup.runtime")

# Plan §5 / §21 — Runtime host + port are hardcoded *internal* values.
# The Runtime is never exposed externally (only the singleton WebUI on
# :const:`WEBUI_PORT` is operator-routable, see :mod:`magi.startup.webui`).
# Binding to loopback on a non-WebUI port keeps a single-process MAGI
# isolated from any network listener on the host.

_RUNTIME_HOST: str = RUNTIME_HOST
_RUNTIME_PORT: int = RUNTIME_PORT
_DEFAULT_LOG_LEVEL: str = DEFAULT_LOG_LEVEL


@dataclass(slots=True)
class RuntimeContext:
    """The one BUS, worker registry, and immutable spec of one node process."""

    startup: StartupContext
    bus: "Bus"
    workers: "WorkerRegistry"

    @classmethod
    def create(cls, startup: StartupContext) -> "RuntimeContext":
        from magi.startup.workers import WorkerRegistry

        bus = _build_bus(startup)
        return cls(
            startup=startup,
            bus=bus,
            workers=WorkerRegistry(
                bus,
                enabled_channels=_build_channels(startup, bus),
                magi_id=_to_magi_id(startup.magi_id),
            ),
        )

    @asynccontextmanager
    async def running(self):
        await self.workers.start()
        try:
            yield self
        finally:
            await self.workers.stop()


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


async def run_magi(config: StartupConfig) -> None:
    """Run one MAGI process until interrupted.

    Blocking — uvicorn.serve() blocks the event loop. The function does
    not return until uvicorn is asked to shut down.
    """
    spec = load_runtime_spec(config.workspace_dir)
    if spec.magi_name != config.magi_name:
        raise RuntimeError(
            f"runtime spec belongs to {spec.magi_name!r}, not {config.magi_name!r}"
        )
    startup = StartupContext(
        host_workspace_dir=config.host_workspace_dir,
        workspace_dir=config.workspace_dir,
        magi_name=spec.magi_name,
        magi_id=spec.magi_id,
        magis_database_url=spec.magis_database_url,
        private_database_url=resolve_private_database_url(config.workspace_dir),
        is_first_magi=spec.is_first_magi,
        runtime_port=spec.runtime_port,
    )

    context = RuntimeContext.create(startup)
    async with context.running():
        await _serve_runtime_api(context)


# ----------------------------------------------------------------------
# Bus wiring
# ----------------------------------------------------------------------


def _build_bus(startup: StartupContext) -> "Bus":
    """Construct the single Bus facade for this process.

    Paths are resolved by :class:`StartupContext` and passed through
    explicitly.  The runtime never bootstraps the retired ``magi.bus``
    facade or shares its process-global state.
    """
    from magi.bus.bootstrap import Bus, open_bus

    state_dir = str(startup.workspace_dir / "memories")
    return open_bus(
        state_dir=state_dir,
        magis_url=startup.magis_database_url,
    )


def _to_magi_id(raw: str) -> int | None:
    """Parse the magi_id string from StartupContext into an int."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Workers
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------


def _build_channels(
    _startup: StartupContext,
    bus: "Bus | None" = None,
) -> list[str]:
    """Resolve enabled message channels from bus settings_book.

    Channels state lives in ``settings_book.channels.enabled`` per the
    runtime convention — no ``MAGI_CHANNELS`` env var.

    Reads the explicitly injected Bus only.
    """
    import json

    try:
        raw = bus.settings_book.get(key="channels.enabled")
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [c for c in parsed if isinstance(c, str)]
    except Exception:  # noqa: BLE001
        logger.warning("could not read channels.enabled from Bus", exc_info=True)
    return []


# ----------------------------------------------------------------------
# HTTP serve
# ----------------------------------------------------------------------


async def _serve_runtime_api(
    context: RuntimeContext,
) -> None:
    """Serve the private Runtime FastAPI app in the active event loop.

    Per plan §21 — host + port are hardcoded; reload is mode-aware
    (CLI + K8s-dev default on, K8s production default off).
    """
    host = _RUNTIME_HOST  # internal host only; not externally exposed
    port = context.startup.runtime_port
    reload = _reload_enabled()
    log_level = _log_level(context.bus)
    reload_dirs = _reload_dirs() if reload else None

    from magi.channels.api.app import create_runtime_app

    config = uvicorn.Config(
        create_runtime_app(context=context),
        factory=False,
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
        reload_dirs=reload_dirs,
    )
    await uvicorn.Server(config).serve()


def _reload_enabled() -> bool:
    """Mode-aware reload toggle.

    Explicit ``MAGI_DEV_RELOAD=1`` / ``0`` always wins.
    When unset, the default depends on the deployment mode:

    * CLI / local (not in Kubernetes) → **on**
    * K8s production (``MAGI_ENV=production``) → **off**
    * K8s dev (no ``MAGI_ENV=production``) → **on**
    """
    explicit = os.environ.get("MAGI_DEV_RELOAD")
    if explicit == "1":
        return True
    if explicit == "0":
        return False

    # Default behaviour — mode-aware
    from magi.startup.paths import is_kubernetes_mode

    if not is_kubernetes_mode():
        # CLI / local development → reload is helpful
        return True

    # In Kubernetes: production deploys set MAGI_ENV=production
    if os.environ.get("MAGI_ENV") == "production":
        return False

    # K8s dev → reload on
    return True


def _log_level(bus: "Bus") -> str:
    """Read DB-driven log level if present, fall back to default.

    Reads the explicitly injected Bus only.
    """
    try:
        raw = bus.settings_book.get(key="system.log_level")
        if raw and raw in {"debug", "info", "warning", "error"}:
            return raw
    except Exception:  # noqa: BLE001
        logger.warning("could not read system.log_level from Bus", exc_info=True)
    return _DEFAULT_LOG_LEVEL


def _reload_dirs() -> list[str]:
    """Resolve uvicorn's reload directory — the package root."""
    import magi

    return [str(Path(magi.__file__).resolve().parent)]


__all__ = [
    "RuntimeContext",
    "run_magi",
]
