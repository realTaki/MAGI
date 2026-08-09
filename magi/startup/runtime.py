"""Unified MAGI runtime composition (plan §14).

The :func:`run_magi` function is the single composition root for one
MAGI process. It:

1. Reads and validates the provisioned :class:`RuntimeSpec`.
2. Opens one :class:`~magi.bus.Bus` facade.
3. Brings up durable workers in dependency order.
4. Brings up channels.
5. Serves the private runtime HTTP API on the spec's sticky port.

It does **not**:

- Spawn subprocesses (use :mod:`magi.startup.local`).
- Create Kubernetes resources (use :mod:`magi.startup.kubernetes`).
- Manage the WebUI (use :mod:`magi.startup.webui`).
- Read or mutate environment variables at runtime.
- Allow runtime-side port / host configuration (plan §21).

Per plan §21, ``reload`` is mode-aware (see :func:`_reload_enabled`):

- ``MAGI_DEV_RELOAD=1/0`` — per-invocation override (highest priority)
- ``MAGI_RELOAD=1/0`` — deploy-configured (k8s ConfigMaps, Dockerfiles)
- Default (neither set) — **on** everywhere
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
        _validate_runtime_identity(startup, bus)
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


def _validate_runtime_identity(startup: StartupContext, bus: "Bus") -> None:
    """Reject a mismatched spec or sticky-port conflict before workers listen."""
    magi_id = _to_magi_id(startup.magi_id)
    if magi_id is None or bus.memberships_book is None:
        raise RuntimeError("runtime identity is missing from the provisioned MAGIS store")
    if bus.memberships_book.get(magi_id=magi_id) is None:
        raise RuntimeError(f"runtime identity {startup.magi_id!r} is not registered in MAGIS")

    runtimes = bus.control_runtimes_book
    ports = bus.port_allocations_book
    runtime = runtimes.get(runtime_id=magi_id) if runtimes is not None else None
    allocation = ports.get(runtime_id=magi_id) if ports is not None else None
    if runtime is None or allocation is None:
        raise RuntimeError(f"runtime {magi_id} has no provisioned control-plane record")
    if runtime.backend_ref != startup.magi_name:
        raise RuntimeError(
            f"runtime spec name {startup.magi_name!r} does not match registered node {runtime.backend_ref!r}"
        )
    if runtime.port != startup.runtime_port or allocation.port != startup.runtime_port:
        raise RuntimeError(
            f"runtime spec port {startup.runtime_port} conflicts with its sticky control-plane allocation"
        )


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

    Per plan §21 — host + port are hardcoded; reload is decided by
    :func:`_reload_enabled` (mode-aware: deploy configs or defaults).
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

    Resolution order:

    1. ``MAGI_DEV_RELOAD=1`` or ``0`` — per-invocation override for dev
       workflows (highest priority).
    2. ``MAGI_RELOAD=1`` or ``0`` — deploy-configured signal (k8s
       ConfigMaps, Dockerfiles).  Production base sets ``"0"``; dev
       overlays override to ``"1"``.
    3. Default (neither set) — **on** everywhere.  CLI / local and
       unconfigured K8s deployments both get hot reload.
    """
    # 1. Per-invocation dev override
    dev = os.environ.get("MAGI_DEV_RELOAD")
    if dev == "1":
        return True
    if dev == "0":
        return False

    # 2. Deploy-configured signal (k8s ConfigMaps, Dockerfiles)
    cfg = os.environ.get("MAGI_RELOAD")
    if cfg == "1":
        return True
    if cfg == "0":
        return False

    # 3. Default — on
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
