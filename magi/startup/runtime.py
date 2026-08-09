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

Per plan §21, ``reload`` is decided by a single explicit env knob:

- Unset (production default) — reload off
- ``MAGI_DEV_RELOAD=1`` (set only by the development entry point) — reload on

Operators cannot flip reload via the standard CLI. See
:func:`_reload_enabled` for the implementation.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from magi.startup.bootstrap import bootstrap_magi
from magi.startup.config import (
    DEFAULT_LOG_LEVEL,
    RUNTIME_HOST,
    RUNTIME_PORT,
    StartupConfig,
    StartupContext,
)

logger = logging.getLogger("magi.startup.runtime")

# Plan §5 / §21 — Runtime host + port are hardcoded *internal* values.
# The Runtime is never exposed externally (only the singleton WebUI on
# :const:`WEBUI_PORT` is operator-routable, see :mod:`magi.startup.webui`).
# Binding to loopback on a non-WebUI port keeps a single-process MAGI
# isolated from any network listener on the host.

_RUNTIME_HOST: str = RUNTIME_HOST
_RUNTIME_PORT: int = RUNTIME_PORT
_DEFAULT_LOG_LEVEL: str = DEFAULT_LOG_LEVEL


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


async def run_magi(config: StartupConfig) -> None:
    """Run one MAGI process until interrupted.

    Blocking — uvicorn.serve() blocks the event loop. The function does
    not return until uvicorn is asked to shut down.
    """
    startup = bootstrap_magi(config)

    bus = _build_bus(startup)
    workers = _build_workers()
    channels = _build_channels(startup, bus)

    async with _runtime_lifespan(
        workers, channels, bus,
        magi_id=_to_magi_id(startup.magi_id),
    ):
        _serve_runtime_api(startup, bus)


# ----------------------------------------------------------------------
# Bus wiring
# ----------------------------------------------------------------------


def _build_bus(startup: StartupContext) -> "Bus":
    """Construct the single Bus facade for this process.

    Paths are resolved by :class:`StartupContext` and passed through
    explicitly.  The runtime never bootstraps the retired ``magi.bus``
    facade or shares its process-global state.
    """
    from magi.bus.bootstrap import Bus, bootstrap_bus

    state_dir = str(startup.workspace_dir / "memories")
    return bootstrap_bus(
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


def _build_workers() -> "WorkerHandles":
    """Local handle set — actual start/stop lives in :func:`_runtime_lifespan`."""
    return WorkerHandles()


@asynccontextmanager
async def _runtime_lifespan(
    workers: "WorkerHandles",
    channels: list[str],
    bus: "Bus",
    *,
    magi_id: int | None = None,
):
    """Start / stop the durable worker pool + message channels.

    Provider worker goes first (so it can drain orphans from a previous
    crash); proactive worker goes last (so everything else is ready).

    ``bus`` is required for :func:`start_provider_worker` — the
    provider worker has been migrated to bus; the rest of the
    workers still use the BUS global singleton internally.

    ``magi_id`` is passed through to the proactive worker for
    Adam-dependent bootstrap checks.
    """
    from magi.agent.worker import start_agent_worker, stop_agent_worker
    from magi.channels import start_channel_workers, stop_channel_workers
    from magi.mcp.worker import start_mcp_worker, stop_mcp_worker
    from magi.proactive.worker import start_proactive_worker, stop_proactive_worker
    from magi.providers.worker import start_provider_worker, stop_provider_worker
    from magi.tools.worker import start_tool_worker, stop_tool_worker

    import magi.channels
    magi.channels.set_current_bus(bus)

    # Startup order: providers → tools → mcp → agent → channels → proactive
    await start_provider_worker(bus=bus)
    await start_tool_worker(bus=bus)
    await start_mcp_worker(bus=bus)
    await start_agent_worker(bus=bus)

    channel_workers = await start_channel_workers(bus, enabled=set(channels))

    await start_proactive_worker(bus=bus, magi_id=magi_id)
    try:
        yield
    finally:
        await stop_proactive_worker()
        await stop_channel_workers(channel_workers)
        await stop_agent_worker()
        await stop_mcp_worker()
        await stop_tool_worker()
        await stop_provider_worker()


class WorkerHandles:
    """Marker — actual worker refs are bound by :func:`_runtime_lifespan`."""


@asynccontextmanager
async def worker_lifespan():
    """Standalone durable worker pool — usable from the WebUI ASGI app.

    Plan §20.1 — this replaces :func:`magi.launcher.worker_lifespan`.
    The :func:`channels.api.app` FastAPI lifespan pulls in the same set
    of workers without dragging the Runtime's uvicorn into the picture.

    Builds its own :class:`Bus` from the active workspace so the
    provider worker (now on bus) has a bus to claim from.
    """
    from magi.agent.worker import start_agent_worker, stop_agent_worker
    from magi.channels import start_channel_workers, stop_channel_workers
    from magi.mcp.worker import start_mcp_worker, stop_mcp_worker
    from magi.proactive.worker import start_proactive_worker, stop_proactive_worker
    from magi.providers.worker import start_provider_worker, stop_provider_worker
    from magi.tools.worker import start_tool_worker, stop_tool_worker
    from magi.bus.bootstrap import bootstrap_bus
    from magi.startup.paths import resolve_workspace_dir

    state_dir = str(resolve_workspace_dir() / "memories")
    bus = bootstrap_bus(state_dir=state_dir)

    import magi.channels
    magi.channels.set_current_bus(bus)

    await start_provider_worker(bus=bus)
    await start_tool_worker(bus=bus)
    await start_mcp_worker(bus=bus)
    await start_agent_worker(bus=bus)

    channel_workers = await start_channel_workers(bus, enabled={"webui"})

    await start_proactive_worker(bus=bus, magi_id=None)
    try:
        yield
    finally:
        await stop_proactive_worker()
        await stop_channel_workers(channel_workers)
        await stop_agent_worker()
        await stop_mcp_worker()
        await stop_tool_worker()
        await stop_provider_worker()


# ----------------------------------------------------------------------
# Channel lifecycle (Telegram for now — extensible)
# ----------------------------------------------------------------------


# [plan amendment §11]: old start_channel/stop_channel/is_channel_running
# deleted — Channel Workers now own their lifecycle via
# magi.channels module-level singletons. The WebUI
# POST /api/channels toggle path now reads
# `workers._registry[name].start(bus)` / `.stop()`.
# Keeping stub wrappers for backward compat during migration.


def start_channel(name: str) -> None:
    """Deprecated — Channel Workers own lifecycle now."""
    import warnings
    warnings.warn(
        "start_channel is deprecated; Channel Workers own lifecycle now",
        DeprecationWarning, stacklevel=2,
    )
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot
        start_bot()


def stop_channel(name: str) -> None:
    """Deprecated — Channel Workers own lifecycle now."""
    import warnings
    warnings.warn(
        "stop_channel is deprecated; Channel Workers own lifecycle now",
        DeprecationWarning, stacklevel=2,
    )
    if name == "telegram":
        from magi.channels.telegram.bot import stop_bot
        stop_bot()


def is_channel_running(name: str) -> bool:
    """Deprecated — Channel Workers own lifecycle now."""
    if name == "telegram":
        from magi.channels.telegram.bot import is_running
        return is_running()
    if name == "webui":
        return True
    try:
        from magi.channels import registered_channel_workers
        workers = registered_channel_workers()
        w = workers.get(name)
        return w is not None and w._task is not None and not w._task.done()
    except Exception:
        return False


# ----------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------


def _build_channels(
    _startup: StartupContext,
    bus: "Bus | None" = None,
) -> list[str]:
    """Resolve enabled message channels from BUS settings_book.

    Channels state lives in ``settings_book.channels.enabled`` per the
    runtime convention — no ``MAGI_CHANNELS`` env var.

    Reads the explicitly injected Bus only.
    """
    import json

    try:
        raw = bus.settings_book.get("channels.enabled")
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


def _serve_runtime_api(
    _startup: StartupContext,
    bus: "Bus | None" = None,
) -> None:
    """Run uvicorn with the private Runtime FastAPI app.

    Per plan §21 — host + port are hardcoded; reload is decided by the
    deployment role, not by an operator-controlled env var.
    """
    host = _RUNTIME_HOST  # internal host only; not externally exposed
    port = _RUNTIME_PORT
    reload = _reload_enabled()
    log_level = _log_level(bus)
    reload_dirs = _reload_dirs() if reload else None

    uvicorn.run(
        "magi.channels.api.app:create_runtime_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
        reload_dirs=reload_dirs,
    )


def _reload_enabled() -> bool:
    """Reload toggle — opt-in via the development entry point.

    Returns ``True`` only when :envvar:`MAGI_DEV_RELOAD` is explicitly
    set to ``"1"`` by the development entry point. Production / operator
    shells cannot flip reload via this knob.
    """
    return os.environ.get("MAGI_DEV_RELOAD") == "1"


def _log_level(bus: "Bus") -> str:
    """Read DB-driven log level if present, fall back to default.

    Reads the explicitly injected Bus only.
    """
    try:
        raw = bus.settings_book.get("system.log_level")
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
    "run_magi",
    "WorkerHandles",
    "worker_lifespan",
    "start_channel",
    "stop_channel",
    "is_channel_running",
]
