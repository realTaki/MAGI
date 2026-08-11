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
from typing import TYPE_CHECKING

import uvicorn

from magi.bus.db.base import utcnow_naive
from magi.bus.library.magis.runtimeBook import (
    RuntimeDesiredState,
    RuntimeObservedState,
)
from magi.startup.config import (
    DEFAULT_LOG_LEVEL,
    RUNTIME_HOST,
    RUNTIME_PORT,
    StartupConfig,
    StartupContext,
)
from magi.startup.paths import resolve_private_database_url
from magi.startup.spec import load_runtime_spec

if TYPE_CHECKING:
    from magi.bus.bootstrap import Bus
    from magi.startup.workers import WorkerRegistry

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
    bus: Bus
    workers: WorkerRegistry

    @classmethod
    def create(cls, startup: StartupContext) -> RuntimeContext:
        from magi.startup.workers import WorkerRegistry

        bus = _build_bus(startup)
        _validate_runtime_identity(startup, bus)

        # Announce ourselves to the control plane: flip
        # runtime_state to STARTED + record our PID so the
        # singleton WebUI's /api/auth/available-magi endpoint can
        # include us in the login dropdown.  This is the runtime's
        # half of the "control registry exposes its DTO query
        # through Bus" handshake.
        magi_id = _to_magi_id(startup.magi_id)
        runtimes = bus.runtime_state_book
        if magi_id is not None and runtimes is not None:
            runtimes.set_desired_state(
                runtime_id=magi_id,
                desired_state=RuntimeDesiredState.STARTED,
            )
            runtimes.set_observed_state(
                runtime_id=magi_id,
                observed_state=RuntimeObservedState.STARTED,
                pid=os.getpid(),
                spawned_at=utcnow_naive(),
            )
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


def _startup_context(config: StartupConfig) -> StartupContext:
    """Resolve one provisioned node before its ASGI app is built."""
    spec = load_runtime_spec(config.workspace_dir)
    if spec.magi_name != config.magi_name:
        raise RuntimeError(f"runtime spec belongs to {spec.magi_name!r}, not {config.magi_name!r}")
    return StartupContext(
        host_workspace_dir=config.host_workspace_dir,
        workspace_dir=config.workspace_dir,
        magi_name=spec.magi_name,
        magi_id=spec.magi_id,
        magis_name=spec.magis_name,
        magis_database_url=spec.magis_database_url,
        private_database_url=resolve_private_database_url(config.workspace_dir),
        is_first_magi=spec.is_first_magi,
        runtime_port=spec.runtime_port,
    )


def _publish_runtime_config(config: StartupConfig) -> None:
    """Make the explicitly selected node available to a reload child.

    Uvicorn's reload supervisor imports the ASGI factory in a fresh Python
    process.  Its only stable input channel is the child environment, so copy
    the already-resolved CLI configuration there before the supervisor starts.
    The runtime spec remains the source of identity and database URLs.
    """
    os.environ["HOST_WORKSPACE_DIR"] = str(config.host_workspace_dir)
    os.environ["MAGI_NAME"] = config.magi_name
    os.environ["MAGIS_NAME"] = config.magis_name
    if config.magis_database_url is None:
        os.environ.pop("MAGIS_DATABASE_URL", None)
    else:
        os.environ["MAGIS_DATABASE_URL"] = config.magis_database_url
    # ``MAGI_ID`` is only set when the CLI passed ``--magi-id``;
    # the single-machine ``magi node run`` path reads it from
    # the workspace's ``runtime.json`` instead. Fall back so
    # the spawned child can still see its identity.
    spec_magi_id = load_runtime_spec(config.workspace_dir).magi_id
    effective_magi_id = config.magi_id or spec_magi_id
    if effective_magi_id:
        os.environ["MAGI_ID"] = effective_magi_id
        # The webui's proxy layer signs every request with
        # ``MAGI_CONTROL_SECRET`` plus the target runtime id;
        # the runtime verifies that ``X-MAGI-Proxy-Target``
        # matches its own ``MAGI_RUNTIME_ID``. The single-
        # machine install needs both. (K8s sets this on the
        # pod via the deployment manifest; the local CLI
        # path was missing it.)
        os.environ["MAGI_RUNTIME_ID"] = effective_magi_id
    else:
        os.environ.pop("MAGI_ID", None)
        os.environ.pop("MAGI_RUNTIME_ID", None)


def create_runtime_app_from_environment():
    """Uvicorn reload factory for one MAGI Runtime API.

    Constructing :class:`RuntimeContext` runs the BUS schema barrier before
    the FastAPI app or any worker becomes available.  The app lifespan then
    owns worker start/stop, which means every Uvicorn reload gets a fresh,
    correctly ordered lifecycle.
    """
    config = StartupConfig.from_env()
    context = RuntimeContext.create(_startup_context(config))

    from magi.channels.api.app import create_runtime_app

    app = create_runtime_app(context=context)

    @asynccontextmanager
    async def _runtime_lifespan(_app):
        async with context.running():
            yield

    app.router.lifespan_context = _runtime_lifespan
    return app


def run_magi(config: StartupConfig) -> None:
    """Run one MAGI with a real Uvicorn reload supervisor when enabled.

    The ASGI application must be an import-string factory for Uvicorn to
    supervise code changes.  On every spawned/reloaded child, the factory
    above synchronises the database before it starts workers or serves HTTP.
    """
    startup = _startup_context(config)
    _publish_runtime_config(config)
    reload = _reload_enabled()
    reload_dirs = _reload_dirs() if reload else None
    uvicorn.run(
        "magi.startup.runtime:create_runtime_app_from_environment",
        factory=True,
        host=_RUNTIME_HOST,
        port=startup.runtime_port,
        log_level=_DEFAULT_LOG_LEVEL,
        reload=reload,
        reload_dirs=reload_dirs,
    )


# ----------------------------------------------------------------------
# Bus wiring
# ----------------------------------------------------------------------


def _build_bus(startup: StartupContext) -> Bus:
    """Construct the single Bus facade for this process.

    Paths are resolved by :class:`StartupContext` and passed through
    explicitly.  The runtime never bootstraps the retired ``magi.bus``
    facade or shares its process-global state.
    """
    from magi.bus.bootstrap import open_bus

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


def _validate_runtime_identity(startup: StartupContext, bus: Bus) -> None:
    """Reject a mismatched spec or sticky-port conflict before workers listen."""
    magi_id = _to_magi_id(startup.magi_id)
    if magi_id is None or bus.memberships_book is None:
        raise RuntimeError("runtime identity is missing from the provisioned MAGIS store")
    if bus.memberships_book.get(magi_id=magi_id) is None:
        raise RuntimeError(f"runtime identity {startup.magi_id!r} is not registered in MAGIS")

    runtimes = bus.runtime_state_book
    runtime = runtimes.get(runtime_id=magi_id) if runtimes is not None else None
    if runtime is None:
        raise RuntimeError(f"runtime {magi_id} has no provisioned control-plane record")
    if runtime.port_in_use_since is None:
        raise RuntimeError(f"runtime {magi_id} has no sticky port allocation")
    if runtime.backend_ref != startup.magi_name:
        raise RuntimeError(
            f"runtime spec name {startup.magi_name!r} does not match registered node {runtime.backend_ref!r}"
        )
    if runtime.port != startup.runtime_port:
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
    bus: Bus | None = None,
) -> list[str]:
    """Resolve enabled message channels from bus settings_book.

    Channels state lives in ``settings_book.channels.enabled`` per the
    runtime convention — no ``MAGI_CHANNELS`` env var.

    If the setting is missing or unparseable, fall back to the
    required-channel default (``[Channel.WEBUI]``).
    This is the runtime-side counterpart to the provisioning
    default in :mod:`magi.bus.provision` — workspaces provisioned
    before that default was added still get the required
    channels' delivery workers.

    Reads the explicitly injected Bus only.
    """
    import json

    from magi.bus.library.local.tasksBook import Channel

    required = (Channel.WEBUI.value,)
    if bus is None:
        return list(required)

    try:
        raw = bus.settings_book.get(key="channels.enabled")
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                cleaned = [c for c in parsed if isinstance(c, str)]
                if cleaned:
                    for ch in required:
                        if ch not in cleaned:
                            cleaned.append(ch)
                    return cleaned
    except Exception:  # noqa: BLE001
        logger.warning("could not read channels.enabled from Bus", exc_info=True)
    return list(required)


# ----------------------------------------------------------------------
# HTTP serve
# ----------------------------------------------------------------------


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


def _reload_dirs() -> list[str]:
    """Resolve uvicorn's reload directory — the package root."""
    import magi

    return [str(Path(magi.__file__).resolve().parent)]


__all__ = [
    "RuntimeContext",
    "create_runtime_app_from_environment",
    "run_magi",
]
