"""MAGI node — single ``Node`` assembly.

A MAGI process is a node. Configuration is read from the database
(``settings`` table), not from environment variables. The only env
var is ``MAGI_STATE_DIR`` (always ``/workspace/memories`` inside
the container — not user-overridable). Everything else lives in
the SQLite database under ``/workspace/memories/magi.db``.

Boot flow: if this MAGI doesn't know its parent MAGIC (no ``magics``
row references it), it seeds the Genesis MAGIC and becomes Adam.
C6 EVEs will receive their parent MAGIC IP at startup and register.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field

import uvicorn

from magi import __version__
# Top-level import (not lazy): ``NodeConfig.from_env`` is
# called at module-load time by the ``magi --check``
# healthcheck (see deploy/Dockerfile.dev). A lazy import
# inside ``from_env`` itself would happen too late — the
# class body has already executed by then and ``from_env``
# references ``require_state_dir`` as a module global.
# Putting the import here makes the name resolution
# deterministic regardless of which entry point runs
# first.
from magi.agent.db import require_state_dir  # noqa: E402
from magi.channels import Channel  # noqa: E402

logger = logging.getLogger("magi.node")

VALID_ROLES = ("adam", "eve")
VALID_CHANNELS = (Channel.WEBUI, "telegram", Channel.TG)
VALID_STATE_BACKENDS = ("postgres", "sqlite", "auto")

# Channel enable/disable state lives in ``settings.channels.enabled``
# (a JSON array).  This is the single source of truth — no
# MAGI_CHANNELS env var, no auto-detection heuristics.  The onboarding
# flow and Settings → Channels card both write here; the node reads it.
# See also :func:`_read_channels_from_db` and
# :func:`_write_channels_to_db`.
_CHANNELS_SETTINGS_KEY = "channels.enabled"


# Channel → settings-key the wizard writes when the operator
# completes the wizard for that channel. The wizard's
# ``save-bot`` step is the canonical way to bring the TG
# daemon up; node boot never spawns the daemon (it lives in
# the webui worker process so its asyncio loop matches the
# request loop, and spawning at boot would race with
# uvicorn's reload — the second ``start_bot`` invocation hits
# "Updater already running" because the module-global
# ``Application`` is reused after a Python reload).


# -- hard-coded container paths (not env-var configurable) -----------------
_STATE_DIR = "/workspace/memories"
_WEBUI_HOST = "0.0.0.0"
_WEBUI_PORT = 42069

# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class NodeConfig:
    """Minimal config for a MAGI node.  Nearly everything lives in the DB."""

    role: str = "adam"
    channels: tuple[str, ...] = field(default_factory=tuple)
    state_dir: str = _STATE_DIR
    host: str = _WEBUI_HOST
    port: int = _WEBUI_PORT
    reload: bool = False
    log_level: str = "info"

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "NodeConfig":
        """Build config.  The only env var honoured is ``--role`` via
        CLI (see ``magi/__main__.py``); everything else is hardcoded or
        read from the DB at boot.
        """
        # Role from CLI only; defaults to "adam" for the solo-node case.
        role = (os.environ.get("MAGI_NODE_ROLE", "")).strip().lower() or "adam"
        if role not in VALID_ROLES:
            raise ValueError(
                f"MAGI_NODE_ROLE must be one of {VALID_ROLES!r}, got {role!r}"
            )

        # Log level: read from settings if available, fall back to "info".
        # The DB isn't up yet during from_env, so we use a temp default;
        # ``run()`` will re-read and apply it after ORM init.
        log_level = "info"
        try:
            from magi.agent.db.settings import state_get
            db_level = state_get(_STATE_DIR, "system.log_level")
            if db_level and db_level in ("debug", "info", "warning", "error"):
                log_level = db_level
        except Exception:
            pass

        reload = os.environ.get("MAGI_RELOAD", "0") == "1"

        return cls(
            role=role,
            channels=(),
            state_dir=_STATE_DIR,
            host=_WEBUI_HOST,
            port=_WEBUI_PORT,
            reload=reload,
            log_level=log_level,
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


def run() -> None:
    """Boot the node: init SQLite, then launch channels from DB config."""
    cfg = NodeConfig.from_env()
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("node starting",
        extra={"version": __version__, "role": cfg.role})

    state_dir = cfg.state_dir or _STATE_DIR

    # SQLite init — always SQLite, no Postgres switch.
    from magi.agent.db import init_sqlite
    db_path = init_sqlite(state_dir)
    logger.info("sqlite initialised", extra={"path": str(db_path)})

    # Initialise the ORM tables. Idempotent.
    from magi.agent.db import init_orm
    init_orm(state_dir)

    # D.18 — one-shot import of any leftover pre-D.18 JSON
    # session files. Idempotent (INSERT OR IGNORE on the
    # (session_id, message_id) unique constraint), so re-running
    # on every boot is cheap: if no JSON files exist, the glob
    # walks zero files. Sessions that already migrated are
    # skipped via the unique constraint. Corrupt files are
    # logged and left in place for hand-inspection (no silent
    # data loss).
    from pathlib import Path
    from magi.agent.memory.session import migrate_from_json
    from magi.agent.workspace import workspace_root
    migrate_from_json(Path(workspace_root(state_dir)))

    # Bootstrap the workspace (skills/, memories/, SOUL.md) before
    # any channel launches. Idempotent — every boot re-checks but
    # only creates what's missing, so deployer edits to SOUL.md
    # (or anything else) survive across restarts.
    from magi.agent.workspace import bootstrap_workspace, workspace_root
    bootstrap_workspace(workspace_root(state_dir))

    # D.X — bootstrap MCP servers declared in the
    # ``mcp_servers`` table. The loader is sync at the
    # boot layer because the rest of ``run()`` is sync;
    # ``bootstrap_mcp_tools`` internally spins a private
    # event loop. Errors degrade to "no MCP tools" so a
    # misconfigured server never blocks startup. The
    # agent loop's :func:`maybe_reload_mcp_tools` is
    # what makes operator edits in the WebUI take effect
    # on the next chat turn.
    try:
        from magi.agent.tools.registry import bootstrap_mcp_tools
        bootstrap_mcp_tools()
    except Exception as e:  # noqa: BLE001 — never block boot
        logger.warning("MCP bootstrap skipped: %s", e)

    # Start the proactive task scheduler. ``start_scheduler``
    # is non-fatal — if apscheduler / MCP / DB had a
    # transient issue we keep going; tasks will simply
    # not fire until the next node restart. The
    # dedicated event loop lives on its own thread so
    # long-running tasks can never stall a request
    # handler.
    try:
        from magi.agent.proactive.scheduler import start_scheduler
        start_scheduler(state_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("scheduler bootstrap skipped: %s", e)

    # Force-initialise the SKILL.md loader so the boot
    # log names every registered skill in one place, and
    # so the very first chat turn already sees the
    # ``load_skill`` tool + system-prompt block. We could
    # defer this to first-use (the loader is a lazy
    # singleton), but the boot-time scan is the cheapest
    # place for an operator to notice a malformed
    # SKILL.md — fail loud, fail early.
    try:
        from magi.agent.tools.skill_loader import get_skill_loader
        loader = get_skill_loader()
        logger.info(
            "skills: %d registered (workspace=%s)",
            len(loader.list()),
            loader._workspace_root,  # noqa: SLF001
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("skills bootstrap skipped: %s", e)

    # Register a shutdown hook so a SIGTERM / uvicorn
    # lifespan teardown drains the executor + closes
    # the cron scheduler cleanly. Atexit is a backup for
    # bare ``magi.node.run`` callers.
    try:
        import atexit
        from magi.agent.proactive.scheduler import stop_scheduler
        atexit.register(stop_scheduler)
    except Exception:  # noqa: BLE001
        pass

    # Read channel enable/disable state from the DB.
    channels = _read_channels_from_db(state_dir)
    if not channels:
        logger.warning("no channels enabled; exiting")
        return

    # Launch non-blocking channels first, THEN blocking ones.
    non_blocking: list[str] = []
    blocking: list[str] = []
    for channel in channels:
        (blocking if channel == Channel.WEBUI else non_blocking).append(channel)

    logger.info(
        "channels resolved: non_blocking=%s blocking=%s",
        non_blocking, blocking,
    )
    for channel in non_blocking + blocking:
        _launch_channel(channel, cfg)


def _init_state(state_dir: str) -> None:
    """Create the SQLite file under ``state_dir``.  Always SQLite."""
    from magi.agent.db import init_sqlite
    db_path = init_sqlite(state_dir)
    logger.info("sqlite initialised", extra={"path": str(db_path)})


# ----------------------------------------------------------------------
# channel launchers — each is blocking; multi-channel concurrency lands
# in C3 once the Telegram runtime exists (asyncio.gather).
# ----------------------------------------------------------------------
def _launch_webui(cfg: NodeConfig) -> None:
    """Mount the WebUI channel: serve FastAPI on the hardcoded port."""
    from magi.channels.webui.app import create_app
    host = cfg.host
    port = cfg.port
    reload_dirs = ["/app/magi"] if cfg.reload else None

    uvicorn.run(
        "magi.channels.webui.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=cfg.log_level,
        reload=cfg.reload,
        reload_dirs=reload_dirs,
    )


def _launch_telegram(cfg: NodeConfig) -> None:
    """Mount the Telegram channel: start the bot polling daemon."""
    from magi.channels.telegram.bot import start_bot

    thread = start_bot(state_dir)
    if thread is None:
        logger.info("telegram: bot token not saved yet — channel idle")
        return
    logger.info("telegram channel running", extra={"bot_thread": thread.name})


_LAUNCHERS = {
    Channel.WEBUI: _launch_webui,
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
    from magi.agent.db.settings import state_get, state_set
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
