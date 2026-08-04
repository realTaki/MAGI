"""The single executable entry point for MAGI services.

A MAGI process is a node. Private runtime settings and state live in the
SQLite database under ``/workspace/memories/magi.db``.  Organisation identity,
instructions and provider configuration live in the direct MAGIS PostgreSQL
database. Hardcoded paths live in :mod:`magi.constants`.

``MAGI_NODE_ROLE`` and ``MAGI_RUNTIME_ID`` bind a running container to its
deployment identity. ``MAGIS_DATABASE_URL`` identifies the one direct MAGIS
database an isolated runtime may read. The orchestrator injects only that URL
and the non-secret runtime identity, never an instruction bundle or provider
credential environment variable.

Boot flow: the first ADAM workspace seeds Genesis.  An EVA workspace never
seeds a Council or a second ADAM; it only initialises its local runtime state.
"""

from __future__ import annotations

import json
import logging
import os
import argparse
import sys
from dataclasses import asdict, dataclass, field

import uvicorn

from magi import __version__
from magi.constants import DEFAULT_LOG_LEVEL, STATE_DIR, WEBUI_HOST, WEBUI_PORT
from magi.channels import Channel  # noqa: E402

logger = logging.getLogger("magi")

VALID_CHANNELS = (Channel.WEBUI, "telegram", Channel.TG)

# Channel enable/disable state lives in ``settings.channels.enabled``
# (a JSON array).  This is the single source of truth — no
# MAGI_CHANNELS env var, no auto-detection heuristics.  The onboarding
# flow and Settings → Channels card both write here; the node reads it.
_CHANNELS_SETTINGS_KEY = "channels.enabled"


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class NodeConfig:
    """Minimal config for a MAGI node.  Everything lives in the DB."""
    channels: tuple[str, ...] = field(default_factory=tuple)
    state_dir: str = STATE_DIR
    host: str = WEBUI_HOST
    port: int = WEBUI_PORT
    reload: bool = False
    log_level: str = DEFAULT_LOG_LEVEL
    role: str = "adam"
    runtime_id: str | None = None

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "NodeConfig":
        """Build config.  Everything is hardcoded or read from the DB."""
        # Log level: read from settings if available, fall back to "info".
        log_level = DEFAULT_LOG_LEVEL
        try:
            from magi.bus.db.settings import state_get
            db_level = state_get(STATE_DIR, "system.log_level")
            if db_level and db_level in ("debug", "info", "warning", "error"):
                log_level = db_level
        except Exception:
            pass

        reload = os.environ.get("MAGI_RELOAD", "0") == "1"

        # Dev mode: Vite on 42069 proxies /api to uvicorn on 8000.
        port_raw = os.environ.get("MAGI_PORT")
        port = int(port_raw) if port_raw else WEBUI_PORT

        role = os.environ.get("MAGI_NODE_ROLE", "adam").strip().lower()
        if role not in {"adam", "eve"}:
            raise ValueError("MAGI_NODE_ROLE must be 'adam' or 'eve'")
        runtime_id = os.environ.get("MAGI_RUNTIME_ID") or None
        if role == "eve" and not runtime_id:
            raise ValueError("MAGI_RUNTIME_ID is required for an EVA runtime")

        return cls(
            channels=(),
            state_dir=STATE_DIR,
            host=WEBUI_HOST,
            port=port,
            reload=reload,
            log_level=log_level,
            role=role,
            runtime_id=runtime_id,
        )


# ----------------------------------------------------------------------
# public surface
# ----------------------------------------------------------------------
def check() -> int:
    """Print resolved config as JSON and exit. Used by container probes."""
    cfg = NodeConfig.from_env()
    print(
        json.dumps(
            {"ok": True, "version": __version__, "config": asdict(cfg)},
            indent=2,
            default=_json_default,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the MAGI command-line service entry point."""
    parser = argparse.ArgumentParser(
        prog="magi",
        description="MAGI runtime. Role is derived from the MAGIS tree in the database.",
    )
    parser.add_argument("--version", action="version", version=f"magi {__version__}")
    parser.add_argument(
        "service",
        nargs="?",
        choices=("runtime", "webui", "local"),
        default="runtime",
        help="service role: runtime (default), webui control plane, or `local` (Local Profile launcher — start/status/stop/doctor after the verb)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print resolved config as JSON and exit. Used by container readiness probes.",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.service == "webui":
        run_webui()
    elif args.service == "local":
        return _run_local(argv)
    else:
        run()
    return 0


def _run_local(argv: list[str] | None) -> int:
    """Dispatch ``magi local <verb>`` to the launcher CLI."""
    from magi.launcher.cli import main as local_main

    # Pop the leading "local" so the local CLI sees just its verbs.
    rest = list(argv or [])
    if rest and rest[0] == "local":
        rest = rest[1:]
    return int(local_main(rest))


def run_webui() -> None:
    """Boot the singleton, stateless WebUI control-plane service."""
    logging.basicConfig(
        level=DEFAULT_LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from magi.bus.db.magis import init_magis_public_db

    # Seeding is idempotent and lets the control plane start before the first
    # runtime without maintaining a second service-entry module.
    init_magis_public_db(seed_root=True)
    port = int(os.environ.get("MAGI_PORT", str(WEBUI_PORT)))
    reload = os.environ.get("MAGI_RELOAD", "0") == "1"
    uvicorn.run(
        "magi.channels.api.app:create_control_app",
        factory=True,
        host=WEBUI_HOST,
        port=port,
        log_level=DEFAULT_LOG_LEVEL,
        reload=reload,
        reload_dirs=["/app/magi"] if reload else None,
    )


def run() -> None:
    """Boot one MAGI runtime and its internal, cluster-only HTTP API."""
    cfg = NodeConfig.from_env()
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("node starting", extra={"version": __version__})

    state_dir = cfg.state_dir

    # The composition root is the only place that initialises local storage.
    # Workers and channels receive the public BUS facade after this point.
    from magi.bus import get_bus
    bus = bootstrap(state_dir, initialise_local=True)
    logger.info("local BUS bootstrapped", extra={"state_dir": state_dir})

    # Direct MAGIS PostgreSQL holds identity, memberships, instructions and
    # lifecycle state. The initial ADAM seeds Genesis there; an EVA only
    # opens the public schema assigned by its one direct MAGIS binding.
    from magi.bus.db.magis import init_magis_public_db
    init_magis_public_db(seed_root=cfg.role == "adam")

    # Bootstrap the workspace (skills/, memories/, SOUL.md) before
    # any channel launches. Idempotent.
    from magi.launcher.paths import bootstrap_workspace, workspace_root as wr
    bootstrap_workspace(wr(state_dir))

    # MCP tool discovery moved to the ToolWorker's startup seed
    # (see magi/tools/worker._seed_tools).  The worker loads both
    # built-in and MCP tools before publishing the durable catalog.

    # Install plugins + wire connector events into the
    # plugin bus. Idempotent — the audit_log plugin and
    # calendar connector install themselves on import;
    # we install_all + start the connector bridge here.
    try:
        from magi.plugins.bus import (
            get_bus,
            install_all,
            list_plugins,
            reset_bus,
        )
        from magi.plugins.samples.audit_log import (
            install_audit_log_plugin,
        )
        from magi.launcher import start_connector_bridge, stop_connector_bridge
        reset_bus()  # fresh per-boot singleton
        install_audit_log_plugin()
        install_all(get_bus())
        start_connector_bridge(get_bus())
        try:
            import atexit
            atexit.register(stop_connector_bridge)
        except Exception:  # noqa: BLE001
            pass
        logger.info("plugins installed: %s", list_plugins())
    except Exception as e:  # noqa: BLE001 — never block boot
        logger.warning("plugin bootstrap skipped: %s", e)

    # Load connector instances from ``connector_configs``.
    # Reads the private SQLite; absent table → no-op.
    try:
        from magi.connectors.boot import load_connectors_from_db
        load_connectors_from_db(state_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("connector bootstrap skipped: %s", e)

    # Start the scheduled-task channel (Phase 5: routed via the BUS
    # bridge so ``__main__`` no longer reaches into ``magi.channels.tasks``).
    try:
        bus.task_scheduler.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("scheduler bootstrap skipped: %s", e)

    # Force-initialise the SKILL.md loader so the boot log names
    # every registered skill in one place.
    try:
        from magi.skills import get_skill_metas
        metas = get_skill_metas()
        logger.info("skills: %d registered", len(metas))
    except Exception as e:  # noqa: BLE001
        logger.warning("skills bootstrap skipped: %s", e)

    # Register a shutdown hook.
    try:
        import atexit
        atexit.register(bus.task_scheduler.stop)
    except Exception:  # noqa: BLE001
        pass

    # Read optional messaging channels from the MAGI's private DB. WebUI is
    # now a separate control-plane service, while this runtime API is always
    # started so that service can reach the selected MAGI.
    channels = _read_channels_from_db(state_dir)
    messaging = [channel for channel in channels if channel != Channel.WEBUI]
    logger.info("messaging channels resolved: %s", messaging)
    for channel in messaging:
        _launch_channel(channel, cfg)
    _launch_runtime_api(cfg)


def _init_state(state_dir: str) -> None:
    """Create the SQLite file under ``state_dir``.  Always SQLite."""
    from magi.bus.db import init_sqlite
    db_path = init_sqlite(state_dir)
    logger.info("sqlite initialised", extra={"path": str(db_path)})


# ----------------------------------------------------------------------
# channel launchers
# ----------------------------------------------------------------------
def _launch_runtime_api(cfg: NodeConfig) -> None:
    """Serve private Runtime APIs; React is hosted by ``magi webui``."""
    reload_dirs = ["/app/magi"] if cfg.reload else None

    uvicorn.run(
        "magi.channels.api.app:create_runtime_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        reload=cfg.reload,
        reload_dirs=reload_dirs,
    )


def _launch_telegram(cfg: NodeConfig) -> None:
    """Mount the Telegram channel: start the bot polling daemon."""
    from magi.channels.telegram.bot import start_bot

    thread = start_bot(STATE_DIR)
    if thread is None:
        logger.info("telegram: bot token not saved yet — channel idle")
        return
    logger.info("telegram channel running", extra={"bot_thread": thread.name})


_LAUNCHERS = {
    "telegram": _launch_telegram,
}


def _launch_channel(name: str, cfg: NodeConfig) -> None:
    logger.info("_launch_channel: dispatching %r", name)
    launcher = _LAUNCHERS.get(name)
    if launcher is None:
        logger.error("no launcher registered for channel %r", name)
        return
    launcher(cfg)


# -- DB-driven channel state -----------------------------------------------

def _read_channels_from_db(state_dir: str) -> list[str]:
    """Return the enabled-channels list from ``settings.channels.enabled``.

    On first boot (key absent), seeds [``webui``] — the control plane is
    always on.  The operator can then toggle TG on via the Settings →
    Channels card.
    """
    from magi.bus.db.settings import state_get, state_set
    raw = state_get(state_dir, _CHANNELS_SETTINGS_KEY)
    if not raw:
        # Seed on first boot
        default = [Channel.WEBUI]
        state_set(state_dir, _CHANNELS_SETTINGS_KEY, json.dumps(default))
        return default
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [c for c in parsed if isinstance(c, str) and c in VALID_CHANNELS]
    except (json.JSONDecodeError, TypeError):
        pass
    # Corrupt value — fix with safe default
    default = [Channel.WEBUI]
    state_set(state_dir, _CHANNELS_SETTINGS_KEY, json.dumps(default))
    return default


def start_channel(name: str, state_dir: str) -> None:
    """Start a single channel at runtime (no restart needed)."""
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot
        start_bot(state_dir)


def stop_channel(name: str) -> None:
    """Stop a single channel at runtime."""
    if name == "telegram":
        from magi.channels.telegram.bot import stop_bot
        stop_bot()


def is_channel_running(name: str) -> bool:
    """Check whether a channel is currently active."""
    if name == "telegram":
        from magi.channels.telegram.bot import is_running
        return is_running()
    if name == Channel.WEBUI:
        return True  # WebUI can't be stopped at runtime
    return False


# ----------------------------------------------------------------------
# utils
# ----------------------------------------------------------------------
def _json_default(value: object) -> object:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


if __name__ == "__main__":
    sys.exit(main())
