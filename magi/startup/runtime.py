"""Unified MAGI runtime composition (plan §14).

The :func:`run_magi` function is the single composition root for one
MAGI process. It:

1. Bootstraps identity via :func:`magi.startup.bootstrap.bootstrap_magi`.
2. Builds both :class:`~magi.bus.Bus` (old) and
   :class:`~magi.new_bus.NewBus` (new) facades side-by-side.
3. Brings up durable workers (provider / agent / tool / delivery).
4. Brings up channels (currently: telegram).
5. Serves the private runtime HTTP API on a fixed internal port.

It does **not**:

- Spawn subprocesses (use :mod:`magi.startup.local`).
- Create Kubernetes resources (use :mod:`magi.startup.kubernetes`).
- Manage the WebUI (use :mod:`magi.startup.webui`).
- Read or mutate environment variables at runtime.
- Allow runtime-side port / host / reload configuration (plan §21).

Per plan §21, ``reload`` is hardcoded by deployment role:

- Production (``PRODUCTION`` env / image) — reload off
- Development (``DEVELOPMENT`` env / image) — reload on

We default to production; the development role can override by setting
``MAGI_DEV_RELOAD=1`` (an explicit knob used only by the development
entry point).
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

    old_bus, new_bus = _build_buses(startup)
    workers = _build_workers()
    channels = _build_channels(startup, new_bus)

    async with _runtime_lifespan(workers, channels, new_bus):
        _serve_runtime_api(startup, new_bus)


# ----------------------------------------------------------------------
# Bus wiring
# ----------------------------------------------------------------------


def _build_buses(startup: StartupContext) -> tuple[object, "NewBus"]:
    """Construct both old and new BUS facades for this process.

    The path + DSN come from :class:`StartupContext` and are passed
    through explicitly (plan §10 — no env mutation at runtime).

    Old bus: unchanged, still the active bus for existing workers.
    New bus: wired from the same paths, available for gradual migration.
    """
    from magi.bus import bootstrap as bus_bootstrap
    from magi.bus.db.magis_book.engine import set_injected_magis_engine
    from magi.bus.db.magis_book.local_engine import build as build_local_engine

    from magi.new_bus.bootstrap import NewBus, bootstrap_new_bus

    # --- shared paths (both buses point at the same databases) ---
    state_dir = str(startup.workspace_dir / "memories")

    # --- old bus (existing, unchanged) ---
    magis_engine = build_local_engine(
        startup.workspace_dir.parent.parent / "MAGI_Societies" / "genesis"
    )
    set_injected_magis_engine(magis_engine)
    old_bus = bus_bootstrap(
        initialise_local=True,
        state_dir=state_dir,
        magis_engine=magis_engine,
    )

    # --- new bus (explicit, no global injection, no env reads) ---
    new_bus = bootstrap_new_bus(
        state_dir=state_dir,
        magis_url=startup.magis_database_url,
    )

    return old_bus, new_bus


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
    new_bus: "NewBus | None" = None,
):
    """Start / stop the durable worker pool + message channels.

    Provider worker goes first (so it can drain orphans from a previous
    crash); delivery worker goes last (so it sees everything produced by
    the rest).

    ``new_bus`` is required for :func:`start_provider_worker` — the
    provider worker has been migrated to new_bus; the rest of the
    workers still use the old bus global singleton internally.
    """
    from magi.agent.worker import start_agent_worker, stop_agent_worker
    from magi.channels.delivery import start_delivery_worker, stop_delivery_worker
    from magi.providers.worker import start_provider_worker, stop_provider_worker
    from magi.tools.worker import start_tool_worker, stop_tool_worker

    if "telegram" in channels:
        try:
            from magi.channels.telegram.bot import start_bot

            start_bot()
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram bootstrap skipped: %s", exc)

    await start_provider_worker(bus=new_bus)
    await start_agent_worker()
    await start_tool_worker()
    await start_delivery_worker()
    try:
        yield
    finally:
        await stop_delivery_worker()
        await stop_tool_worker()
        await stop_agent_worker()
        await stop_provider_worker()
        if "telegram" in channels:
            try:
                from magi.channels.telegram.bot import stop_bot

                stop_bot()
            except Exception:  # noqa: BLE001
                pass


class WorkerHandles:
    """Marker — actual worker refs are bound by :func:`_runtime_lifespan`."""


@asynccontextmanager
async def worker_lifespan():
    """Standalone durable worker pool — usable from the WebUI ASGI app.

    Plan §20.1 — this replaces :func:`magi.launcher.worker_lifespan`.
    The :func:`channels.api.app` FastAPI lifespan pulls in the same set
    of workers without dragging the Runtime's uvicorn into the picture.

    Builds its own :class:`NewBus` from the active workspace so the
    provider worker (now on new_bus) has a bus to claim from.
    """
    from magi.agent.worker import start_agent_worker, stop_agent_worker
    from magi.channels.delivery import (
        start_delivery_worker,
        stop_delivery_worker,
    )
    from magi.providers.worker import (
        start_provider_worker,
        stop_provider_worker,
    )
    from magi.tools.worker import start_tool_worker, stop_tool_worker
    from magi.new_bus.bootstrap import bootstrap_new_bus
    from magi.startup.paths import resolve_workspace_dir

    state_dir = str(resolve_workspace_dir() / "memories")
    new_bus = bootstrap_new_bus(state_dir=state_dir)

    await start_provider_worker(bus=new_bus)
    await start_agent_worker()
    await start_tool_worker()
    await start_delivery_worker()
    try:
        yield
    finally:
        await stop_delivery_worker()
        await stop_tool_worker()
        await stop_agent_worker()
        await stop_provider_worker()


# ----------------------------------------------------------------------
# Channel lifecycle (Telegram for now — extensible)
# ----------------------------------------------------------------------


def start_channel(name: str) -> None:
    """Bring one channel up — plan §20.1.

    Replaces :func:`magi.launcher.start_channel`.  Channel adapters stay
    in :mod:`magi.channels.*`; this is the composition-root dispatcher.
    """
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot

        start_bot()


def stop_channel(name: str) -> None:
    """Bring one channel down — plan §20.1."""
    if name == "telegram":
        from magi.channels.telegram.bot import stop_bot

        stop_bot()


def is_channel_running(name: str) -> bool:
    """Return ``True`` if the channel process is currently live.

    Plan §20.1 — replaces :func:`magi.launcher.is_channel_running`.
    """
    if name == "telegram":
        from magi.channels.telegram.bot import is_running

        return is_running()
    return name == "webui"


# ----------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------


def _build_channels(
    _startup: StartupContext,
    new_bus: "NewBus | None" = None,
) -> list[str]:
    """Resolve enabled message channels from BUS settings_book.

    Channels state lives in ``settings_book.channels.enabled`` per the
    runtime convention — no ``MAGI_CHANNELS`` env var.

    Prefers ``new_bus`` when available; falls back to the old bus
    global singleton for backward compatibility.
    """
    import json

    # Try new_bus first (explicitly wired, no global lookup).
    if new_bus is not None:
        try:
            raw = new_bus.settings_book.get("channels.enabled")
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [c for c in parsed if isinstance(c, str)]
        except Exception:  # noqa: BLE001
            pass

    # Fall back to old bus singleton (e.g. worker_lifespan path).
    try:
        from magi.bus import get_bus

        raw = get_bus().settings.get("channels.enabled")
        if not raw:
            return []
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [c for c in parsed if isinstance(c, str)]
    except Exception:  # noqa: BLE001
        return []
    return []


# ----------------------------------------------------------------------
# HTTP serve
# ----------------------------------------------------------------------


def _serve_runtime_api(
    _startup: StartupContext,
    new_bus: "NewBus | None" = None,
) -> None:
    """Run uvicorn with the private Runtime FastAPI app.

    Per plan §21 — host + port are hardcoded; reload is decided by the
    deployment role, not by an operator-controlled env var.
    """
    host = _RUNTIME_HOST  # internal host only; not externally exposed
    port = _RUNTIME_PORT
    reload = _reload_enabled()
    log_level = _log_level(new_bus)
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
    """Production: hardcoded off. Development role: hardcoded on.

    The distinction is encoded in :envvar:`MAGI_DEV_RELOAD` set by the
    development entry point — operators cannot flip it via the
    standard CLI.
    """
    return os.environ.get("MAGI_DEV_RELOAD") == "1"


def _log_level(new_bus: "NewBus | None" = None) -> str:
    """Read DB-driven log level if present, fall back to default.

    Prefers ``new_bus`` when available; falls back to the old bus
    global singleton for backward compatibility.
    """
    # Try new_bus first (explicitly wired, no global lookup).
    if new_bus is not None:
        try:
            raw = new_bus.settings_book.get("system.log_level")
            if raw and raw in {"debug", "info", "warning", "error"}:
                return raw
        except Exception:  # noqa: BLE001
            pass

    # Fall back to old bus singleton.
    try:
        from magi.bus import get_bus

        raw = get_bus().settings.get("system.log_level")
        if raw and raw in {"debug", "info", "warning", "error"}:
            return raw
    except Exception:  # noqa: BLE001
        pass
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