"""The single executable entry point for MAGI services.

A MAGI process is a node. Private runtime settings and state live in the
SQLite database under ``<workspace>/memories/magi.db`` (resolved from
``MAGI_WORKSPACE_DIR`` or ``MAGI_DATA_ROOT``, never a hardcoded path).
Organisation identity, instructions and provider configuration live in the
direct MAGIS database. All path resolution lives in :mod:`magi.launcher.paths`.

``MAGI_RUNTIME_ID`` binds a running container to its deployment identity;
``MAGIS_DATABASE_URL`` identifies the one direct MAGIS database an isolated
runtime may read. The orchestrator injects only that URL and the non-secret
runtime identity, never an instruction bundle or provider credential
environment variable. The runtime's archetype (ADAM vs EVA) is read from
the MAGIS tree (``MAGIS.adam_id == MAGIC.id``); no separate role env var
is needed.

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
from magi.launcher.constants import DEFAULT_LOG_LEVEL, WEBUI_HOST, WEBUI_PORT
from magi.launcher.paths import state_dir
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
    state_dir: str = field(default_factory=lambda: str(state_dir()))
    host: str = WEBUI_HOST
    port: int = WEBUI_PORT
    reload: bool = False
    log_level: str = DEFAULT_LOG_LEVEL
    runtime_id: str | None = None
    is_genesis: bool = False

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "NodeConfig":
        """Build config.  Everything is hardcoded or read from the DB.

        ``MAGI_RUNTIME_ID`` is the only runtime-identity env var. When
        it is unset, this process is the Genesis bootstrap ADAM —
        there is no MAGIC row to look up yet, and the seed will
        create one. When set, the orchestrator has already created
        the MAGIC + MAGIS membership rows; the role is derived from
        MAGIS at boot (``MagisService.derive_runtime_role``), not
        from a separate ``MAGI_NODE_ROLE`` env var.
        """
        # Log level: read from settings if available, fall back to "info".
        log_level = DEFAULT_LOG_LEVEL
        try:
            from magi.bus.db.settings import state_get
            db_level = state_get(str(state_dir()), "system.log_level")
            if db_level and db_level in ("debug", "info", "warning", "error"):
                log_level = db_level
        except Exception:
            pass

        reload = os.environ.get("MAGI_RELOAD", "0") == "1"

        # Dev mode: Vite on 42069 proxies /api to uvicorn on 8000.
        port_raw = os.environ.get("MAGI_PORT")
        port = int(port_raw) if port_raw else WEBUI_PORT

        runtime_id_raw = os.environ.get("MAGI_RUNTIME_ID", "").strip()
        if runtime_id_raw:
            if not runtime_id_raw.isdigit():
                raise ValueError("MAGI_RUNTIME_ID must be an integer magic_id")
            runtime_id = runtime_id_raw
            is_genesis = False
        else:
            runtime_id = None
            is_genesis = True

        return cls(
            channels=(),
            state_dir=str(state_dir()),
            host=WEBUI_HOST,
            port=port,
            reload=reload,
            log_level=log_level,
            runtime_id=runtime_id,
            is_genesis=is_genesis,
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
        nargs="*",
        help="service role: runtime (default), webui control plane, or `local` (Local Profile launcher — start/status/stop/doctor after the verb)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print resolved config as JSON and exit. Used by container readiness probes.",
    )
    args, extras = parser.parse_known_args(argv)
    # Materialise argv so downstream dispatchers (e.g. ``_run_local``)
    # don't depend on whether ``main()`` was called from the console
    # script wrapper (where ``argv`` arrives as ``None``).
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default to runtime when no service is given.
    if not args.service:
        args.service = ["runtime"]
    # Validate the chosen service role; trailing positionals are reserved
    # for the per-service CLI (e.g. ``magi local start``).
    if args.service[0] not in ("runtime", "webui", "local"):
        parser.error(
            f"unknown service {args.service[0]!r} "
            "(expected runtime | webui | local)"
        )
    if args.check:
        return check()
    if args.service[0] == "webui":
        run_webui()
    elif args.service[0] == "local":
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
        reload_dirs=_reload_dirs() if reload else None,
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
    from magi.bus import bootstrap
    bootstrap(initialise_local=True)
    logger.info("local BUS bootstrapped", extra={"state_dir": state_dir})

    # Direct MAGIS PostgreSQL holds identity, memberships, instructions and
    # lifecycle state. The archetype (ADAM / EVA) is read from MAGIS, not
    # from a ``MAGI_NODE_ROLE`` env var:
    #   - Genesis bootstrap (no ``MAGI_RUNTIME_ID``) seeds the public
    #     schema with this process as the root ADAM.
    #   - Orchestrator-launched runtimes (``MAGI_RUNTIME_ID`` set) look
    #     up their MAGIC row + direct MAGIS membership and compare their
    #     ``magic_id`` against ``MAGIS.adam_id`` to derive the role.
    from magi.bus.db.magis import init_magis_public_db
    init_magis_public_db(seed_root=cfg.is_genesis)

    # Bootstrap the workspace (skills/, memories/, SOUL.md) before
    # any channel launches. Idempotent.
    from magi.launcher.paths import bootstrap_workspace, workspace_root as wr
    bootstrap_workspace(wr())

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
        load_connectors_from_db()
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

    # Read optional messaging channels from the BUS. WebUI is
    # now a separate control-plane service, while this runtime API is always
    # started so that service can reach the selected MAGI.
    channels = _read_channels_from_db()
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
def _reload_dirs() -> list[str]:
    """Return the package root that uvicorn should watch when ``MAGI_RELOAD=1``.

    Resolved from the installed ``magi`` package (``import magi;
    Path(magi.__file__).parent``) so the same code path works for every
    deployment shape:

    - container build (``/app/magi``)
    - k8s-dev hostPath bind (``/mnt/magi/magi``)
    - Local Profile editable install (``<uv-tools>/magi/lib/.../magi``)
    - Local Profile checkout (``$MAGI_REPO_ROOT/magi``)

    The previous hardcoded ``["/app/magi"]`` only worked for the
    container image, so a developer running ``magi local start`` saw
    no file-watch and any source edit silently went stale.  This
    helper makes hot reload work in every profile — the operator just
    sets ``MAGI_RELOAD=1`` and saves a file.
    """
    from pathlib import Path

    import magi  # noqa: WPS433 — dynamic import keeps startup cost at the
    # very end of the function so ``magi --version`` still starts fast.

    return [str(Path(magi.__file__).resolve().parent)]


def _launch_runtime_api(cfg: NodeConfig) -> None:
    """Serve private Runtime APIs; React is hosted by ``magi webui``."""
    reload_dirs = _reload_dirs() if cfg.reload else None

    uvicorn.run(
        "magi.channels.api.app:create_runtime_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        reload=cfg.reload,
        reload_dirs=reload_dirs,
    )


def _launch_telegram(_cfg: NodeConfig) -> None:
    """Mount the Telegram channel: start the bot polling daemon."""
    from magi.channels.telegram.bot import start_bot

    thread = start_bot(str(state_dir()))
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

def _read_channels_from_db() -> list[str]:
    """Return the enabled-channels list from ``settings.channels.enabled``.

    On first boot (key absent), seeds [``webui``] — the control plane is
    always on.  The operator can then toggle TG on via the Settings →
    Channels card.

    Reads/writes go through the BUS settings service once the runtime
    is up; the caller is the composition root, which runs after
    ``bootstrap(initialise_local=True)``.
    """
    from magi.bus import get_bus

    bus = get_bus()
    raw = bus.settings.get(_CHANNELS_SETTINGS_KEY)
    if not raw:
        # Seed on first boot
        default = [Channel.WEBUI]
        bus.settings.set(_CHANNELS_SETTINGS_KEY, json.dumps(default))
        return default
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [c for c in parsed if isinstance(c, str) and c in VALID_CHANNELS]
    except (json.JSONDecodeError, TypeError):
        pass
    # Corrupt value — fix with safe default
    default = [Channel.WEBUI]
    bus.settings.set(_CHANNELS_SETTINGS_KEY, json.dumps(default))
    return default


def start_channel(name: str) -> None:
    """Start a single channel at runtime (no restart needed)."""
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot
        start_bot()


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
