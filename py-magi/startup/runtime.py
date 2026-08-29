"""Unified MAGI runtime composition (plan §14).

The :func:`run_magi` function is the single composition root for one
MAGI process. It:

1. Opens one :class:`~bus.Bus` facade for the configured workspace and
   MAGIS database.
2. Reads and validates the provisioned :class:`RuntimeSpec` through that
   same facade.
3. Brings up durable workers in dependency order.
4. Brings up channels.
5. Serves the private runtime HTTP API on the spec's sticky port.

It does **not**:

- Spawn subprocesses (use :mod:`startup.local`).
- Create Kubernetes resources (use :mod:`startup.kubernetes`).
- Manage the WebUI (use :mod:`startup.webui`).
- Select its host, port, or reload behaviour from environment variables.
- Allow runtime-side port / host configuration (plan §21).

The Runtime is deliberately one process for its whole lifetime.  Source
changes take effect after an explicit process restart; there is no Uvicorn
reload supervisor in any deployment mode.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import uvicorn

from old_bus.bases.db.base import utcnow_naive
from old_bus.firmwares.books.magis.runtimeBook import (
    RuntimeDesiredState,
    RuntimeObservedState,
)
from startup import systemd_notify
from startup.config import (
    DEFAULT_LOG_LEVEL,
    RUNTIME_HOST,
    StartupConfig,
    StartupContext,
)
from startup.paths import resolve_private_database_url, resolve_runtime_pid_path
from startup.process import (
    claim_pid_file,
    install_lifecycle_handlers,
    mark_registry_stopped,
)
from startup.spec import load_runtime_spec

if TYPE_CHECKING:
    from old_bus.bootstrap import Bus
    from startup.workers import WorkerRegistry

logger = logging.getLogger("startup.runtime")

# Plan §5 / §21 — Runtime host + port are hardcoded *internal* values.
# The Runtime is never exposed externally (only the singleton WebUI on
# :const:`WEBUI_PORT` is operator-routable, see :mod:`startup.webui`).
# Binding to loopback on a non-WebUI port keeps a single-process MAGI
# isolated from any network listener on the host.

_RUNTIME_HOST: str = RUNTIME_HOST
_DEFAULT_LOG_LEVEL: str = DEFAULT_LOG_LEVEL


@dataclass(slots=True)
class RuntimeContext:
    """The one BUS, worker registry, and immutable spec of one node process."""

    startup: StartupContext
    bus: Bus
    workers: WorkerRegistry

    @classmethod
    def create(cls, startup: StartupContext, bus: Bus) -> RuntimeContext:
        from startup.workers import WorkerRegistry

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


def _startup_context(
    config: StartupConfig, bus: Bus, *, magis_url: str
) -> StartupContext:
    """Resolve one provisioned node before its ASGI app is built.

    Identity is derived from the MAGIS shared database — see
    :func:`startup.spec.load_runtime_spec` for the cross-table
    lookup that ties ``runtime_state.backend_ref`` back to its parent
    MAGIS row.  The MAGIS URL is reconstructed from
    ``host_workspace_dir`` + ``magis_name``; ``resolve_magis_database_url``
    is the single path-aware helper for that.
    """
    spec = load_runtime_spec(
        bus, config.magi_name, magis_database_url=magis_url
    )
    if spec.magi_name != config.magi_name:
        raise RuntimeError(
            f"MAGIS registry row reports {spec.magi_name!r}, "
            f"not {config.magi_name!r}"
        )
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


def _configure_runtime_environment(config: StartupConfig, *, magi_id: str) -> None:
    """Expose the resolved runtime identity to proxy-auth dependencies.

    The provisioned runtime spec remains authoritative.  These environment
    values are only consumed by components that cannot receive the startup
    context directly, notably proxy-auth verification.
    """
    os.environ["HOST_WORKSPACE_DIR"] = str(config.host_workspace_dir)
    os.environ["MAGI_NAME"] = config.magi_name
    os.environ["MAGIS_NAME"] = config.magis_name
    if config.magis_database_url is None:
        os.environ.pop("MAGIS_DATABASE_URL", None)
    else:
        os.environ["MAGIS_DATABASE_URL"] = config.magis_database_url
    os.environ["MAGI_ID"] = magi_id
    # The webui's proxy layer signs every forwarded request with an
    # HMAC derived from the per-MAGIS ``control_secrets`` row; the
    # runtime verifies the same row via its own bus, plus checks that
    # ``X-MAGI-Proxy-Target`` matches its own ``MAGI_RUNTIME_ID``.
    os.environ["MAGI_RUNTIME_ID"] = magi_id


def _create_runtime_app(context: RuntimeContext):
    """Build the Runtime API with workers owned by its lifespan."""

    from channels.api.app import create_runtime_app

    app = create_runtime_app(bus=context.bus, workers=context.workers)

    @asynccontextmanager
    async def _runtime_lifespan(_app):
        async with context.running():
            yield

    app.router.lifespan_context = _runtime_lifespan
    return app


def run_magi(config: StartupConfig) -> None:
    """Run one MAGI Runtime until it is explicitly stopped or restarted.

    Foreground-mode lifecycle (mirrors :func:`startup.local.start_magi`):

    1. Claim ``<workspace>/run/magi.pid`` with ``os.getpid()`` so
       ``magi node stop`` can find this process.  Refuse to start if
       the file points at a still-alive PID.
    2. Install SIGTERM/SIGINT handlers that unlink the PID file and
       flip ``runtime_state.observed_state`` to ``STOPPED`` via
       :func:`startup.process.mark_registry_stopped`.  The
       handler runs once even if uvicorn re-raises the signal.

    The PID file records this single runtime process, so ``magi node stop``
    and ``magi node restart`` always address the process that owns the
    listening socket.
    """
    from old_bus import open_bus
    from startup.paths import resolve_magis_database_url

    magis_url = config.magis_database_url or resolve_magis_database_url(
        config.host_workspace_dir, config.magis_name
    )
    bus = open_bus(workspace_dir=str(config.workspace_dir), magis_url=magis_url)
    startup = _startup_context(config, bus, magis_url=magis_url)
    _configure_runtime_environment(config, magi_id=startup.magi_id)
    context = RuntimeContext.create(startup, bus)
    app = _create_runtime_app(context)
    pid_path = resolve_runtime_pid_path(startup.workspace_dir)
    claim_pid_file(pid_path)
    cleanup = install_lifecycle_handlers(
        pid_path,
        extra_cleanup=lambda: mark_registry_stopped(config),
    )
    # systemd watchdog: only active when ``$WATCHDOG_USEC`` is set
    # by the unit (``WatchdogSec=`` in the .service file).  In every
    # other launch path (CLI, k8s, dev tunnel) the helper is a no-op
    # so we never need to special-case the supervisor.  The ping
    # interval is half the watchdog window per sd_notify(3).
    watchdog_task = _maybe_start_watchdog()
    try:
        uvicorn.run(
            app,
            host=_RUNTIME_HOST,
            port=startup.runtime_port,
            log_level=_DEFAULT_LOG_LEVEL,
        )
    finally:
        systemd_notify.announce_stopping()
        if watchdog_task is not None:
            watchdog_task.cancel()
        # Cleanup if uvicorn returns without a signal (e.g. KeyboardInterrupt
        # inside the asyncio loop that uvicorn swallows).  The signal
        # handler is a no-op the second time around (``fired`` flag).
        cleanup.run_once()


# ----------------------------------------------------------------------
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
    if bus.memberships_book.get(magi_id) is None:
        raise RuntimeError(f"runtime identity {startup.magi_id!r} is not registered in MAGIS")

    runtimes = bus.runtime_state_book
    runtime = runtimes.get_by_runtime_id(runtime_id=magi_id) if runtimes is not None else None
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
    required-channel default (``["webui"]``).
    This is the runtime-side counterpart to the provisioning
    default in :mod:`bus.provision` — workspaces provisioned
    before that default was added still get the required
    channels' delivery workers.

    Reads the explicitly injected Bus only.
    """
    import json

    required = ("webui",)
    if bus is None:
        return list(required)

    try:
        raw = bus.settings_book.get_value(key="channels.enabled")
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


def _maybe_start_watchdog() -> asyncio.Task | None:
    """Start the systemd watchdog ping task iff ``$WATCHDOG_USEC`` is set.

    Returns the :class:`asyncio.Task` so the caller can cancel it on
    shutdown, or ``None`` when no watchdog is active.  Outside systemd
    this is a no-op — the runtime can be launched with ``python -m magi
    node run`` and behave the same as it always did.

    Ping interval is half the watchdog window per sd_notify(3) — a
    missed tick is detectable within one watchdog cycle.
    """
    usec = systemd_notify.watchdog_usec()
    if usec is None or usec <= 0:
        return None
    interval = max(1.0, (usec / 1_000_000.0) / 2.0)

    async def _loop() -> None:
        # First ping straight away so a hung startup fails fast instead
        # of waiting a full watchdog cycle for systemd to notice.
        systemd_notify.watchdog_ping()
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            systemd_notify.watchdog_ping()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(_loop(), name="magi.watchdog")


__all__ = [
    "RuntimeContext",
    "run_magi",
]
