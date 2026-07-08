"""MAGI node — single ``Node`` assembly.

A MAGI process is a node with **independent configuration axes**:

- ``MAGI_NODE_ROLE`` — permission scope preset (``adam`` = enterprise,
  ``eve`` = personal). Acts as a default for a few other axes; every
  axis can still be overridden explicitly.
- ``MAGI_CHANNELS`` — comma-separated list of channel adapters to mount
  (``webui``, ``telegram``, future ``email`` / ``calendar`` …). Any
  role can mount any subset of channels.
- ``MAGI_STATE_BACKEND`` — which persistent store to use
  (``postgres`` | ``sqlite`` | ``auto``). Independent of role.
- ``MAGI_ADAM_URL`` / ``MAGI_SHARED_SECRET`` — how to reach / auth Adam
  for ingest RPC. Any node may need these; not role-gated.

The role only affects the permission gate inside the runtime; it never
picks storage, channels or peers on its own. The presets exist for
operator ergonomics — ``MAGI_NODE_ROLE=adam`` is just a shorthand for
"scope=enterprise, default channels=webui" and ``MAGI_NODE_ROLE=eve``
for "scope=personal, default channels=telegram"; every underlying
field is overridable.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field

import uvicorn

from magi import __version__

logger = logging.getLogger("magi.node")

VALID_ROLES = ("adam", "eve")
VALID_CHANNELS = ("webui", "telegram")
VALID_STATE_BACKENDS = ("postgres", "sqlite", "auto")

# Default channel bundle when ``MAGI_CHANNELS`` is unset. This is the only
# role-driven default that survives — and even it is overridden by an
# explicit ``MAGI_CHANNELS``.
_ROLE_DEFAULT_CHANNELS: dict[str, tuple[str, ...]] = {
    "adam": ("webui",),
    "eve": ("telegram",),
}


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class NodeConfig:
    """Environment-driven config for a single MAGI node.

    All fields are flat and independent. ``role`` is just a tag; every
    other field can be set regardless of role. Anything left ``None``
    means "not relevant to the current channel mix" — it's the absence
    of a requirement, not a coupling.
    """

    role: str
    channels: tuple[str, ...] = field(default_factory=tuple)

    # — always-on (read regardless of role / channels) —
    shared_secret_set: bool = False
    adam_url: str = "http://adam:42069"
    log_level: str = "info"

    # — persistent store (any role, any channel mix) —
    # ``auto`` means "postgres if DATABASE_URL is set, else sqlite".
    state_backend: str = "auto"

    # — WebUI channel (when "webui" in channels) —
    host: str | None = None
    port: int | None = None
    reload: bool = False

    # — Telegram channel (when "telegram" in channels) —
    employee_id: str | None = None
    bot_token_set: bool = False
    state_dir: str | None = None

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "NodeConfig":
        role = (os.environ.get("MAGI_NODE_ROLE", "")).strip().lower()
        if role not in VALID_ROLES:
            raise ValueError(
                f"MAGI_NODE_ROLE must be one of {VALID_ROLES!r}, got {role!r}"
            )

        channels_raw = os.environ.get("MAGI_CHANNELS", "").strip()
        if channels_raw:
            channels = tuple(
                c.strip().lower() for c in channels_raw.split(",") if c.strip()
            )
        else:
            channels = _ROLE_DEFAULT_CHANNELS[role]

        unknown = [c for c in channels if c not in VALID_CHANNELS]
        if unknown:
            raise ValueError(
                f"MAGI_CHANNELS contains unknown channel(s) {unknown!r}; "
                f"valid: {VALID_CHANNELS!r}"
            )

        host = port = None
        if "webui" in channels:
            host = os.environ.get("MAGI_HOST", "0.0.0.0")
            port = int(os.environ.get("MAGI_PORT", "42069"))

        # ``state_dir`` belongs to the node, not to any specific channel —
        # Adam uses it for SQLite state (small / dev), Eve for local working
        # state. Read it unconditionally. Default matches the container's
        # working directory (matches Agent convention).
        state_dir = os.environ.get("MAGI_STATE_DIR", "/workspace/memories")

        employee_id = None
        bot_token_set = False
        if "telegram" in channels:
            employee_id = os.environ.get("MAGI_EMPLOYEE_ID")
            bot_token_set = bool(os.environ.get("MAGI_BOT_TOKEN"))

        return cls(
            role=role,
            channels=channels,
            shared_secret_set=bool(os.environ.get("MAGI_SHARED_SECRET")),
            adam_url=os.environ.get("MAGI_ADAM_URL", "http://adam:42069"),
            log_level=os.environ.get("MAGI_LOG_LEVEL", "info"),
            state_backend=_resolve_state_backend(os.environ.get("MAGI_STATE_BACKEND")),
            host=host,
            port=port,
            reload=os.environ.get("MAGI_RELOAD", "0") == "1",
            employee_id=employee_id,
            bot_token_set=bot_token_set,
            state_dir=state_dir,
        )


def _resolve_state_backend(raw: str | None) -> str:
    """Validate MAGI_STATE_BACKEND; ``auto`` resolves at use time."""
    backend = (raw or "auto").strip().lower()
    if backend not in VALID_STATE_BACKENDS:
        raise ValueError(
            f"MAGI_STATE_BACKEND must be one of {VALID_STATE_BACKENDS!r}, got {raw!r}"
        )
    if backend == "auto":
        return "postgres" if os.environ.get("DATABASE_URL") else "sqlite"
    return backend


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
    """Boot the node: for each enabled channel, run its launcher."""
    cfg = NodeConfig.from_env()
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    logger.info(
        "node starting",
        extra={
            "version": __version__,
            "role": cfg.role,
            "channels": list(cfg.channels),
            "state_backend": cfg.state_backend,
        },
    )

    _init_state(cfg)

    # Initialise the SQLAlchemy ORM tables (Department, Employee,
    # and the future C1.x additions). Idempotent — ``create_all``
    # is a no-op for existing tables, so this is safe on every
    # boot. Runs in the same ``magi.db`` file as the hand-rolled
    # KV store; the two write to disjoint tables.
    from magi.agent.db import init_orm
    init_orm(cfg.state_dir)

    # D.18 — one-shot import of any leftover pre-D.18 JSON
    # session files. Idempotent (INSERT OR IGNORE on the
    # (session_id, message_id) unique constraint), so re-running
    # on every boot is cheap: if no JSON files exist, the glob
    # walks zero files. Sessions that already migrated are
    # skipped via the unique constraint. Corrupt files are
    # logged and left in place for hand-inspection (no silent
    # data loss).
    from pathlib import Path
    from magi.agent.sessions import migrate_from_json
    from magi.agent.workspace import workspace_root
    migrate_from_json(Path(workspace_root(cfg.state_dir)))

    # Bootstrap the workspace (skills/, memories/, SOUL.md) before
    # any channel launches. Idempotent — every boot re-checks but
    # only creates what's missing, so deployer edits to SOUL.md
    # (or anything else) survive across restarts.
    from magi.agent.workspace import bootstrap_workspace, workspace_root
    bootstrap_workspace(workspace_root(cfg.state_dir or "/workspace/memories"))

    # D.X — load any MCP servers declared in mcp.json. The
    # loader is sync at the boot layer because the rest of
    # ``run()`` is sync; ``bootstrap_mcp_tools`` internally
    # spins a private event loop. Errors degrade to "no MCP
    # tools" so a misconfigured MCP config never blocks
    # startup.
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
        start_scheduler(cfg.state_dir)
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

    if not cfg.channels:
        logger.warning("no channels enabled (MAGI_CHANNELS is empty); exiting")
        return

    # Launch non-blocking channels first (they return quickly, often by
    # spawning a background thread or task), THEN launch the blocking one
    # (``webui`` is the only one that holds the main thread via
    # ``uvicorn.run``). If we iterated in user-given order, putting webui
    # first would starve every other channel.
    non_blocking: list[str] = []
    blocking: list[str] = []
    for channel in cfg.channels:
        (blocking if channel == "webui" else non_blocking).append(channel)

    for channel in non_blocking + blocking:
        _launch_channel(channel, cfg)


def _init_state(cfg: NodeConfig) -> None:
    """Bring up the local state backend before any channel starts.

    For ``sqlite`` (the default in C0), creates the SQLite file under
    ``MAGI_STATE_DIR`` and logs the path. Postgres initialisation lands
    alongside the ORM in C1.
    """
    if cfg.state_backend != "sqlite":
        logger.info(
            "state backend %s — deferring init to its own module (C1+)",
            cfg.state_backend,
        )
        return

    state_dir = cfg.state_dir or "/workspace/memories"
    from magi.agent.db import init_sqlite

    db_path = init_sqlite(state_dir)
    logger.info("sqlite initialised", extra={"path": str(db_path)})


# ----------------------------------------------------------------------
# channel launchers — each is blocking; multi-channel concurrency lands
# in C3 once the Telegram runtime exists (asyncio.gather).
# ----------------------------------------------------------------------
def _launch_webui(cfg: NodeConfig) -> None:
    """Mount the WebUI channel: serve FastAPI on cfg.host:cfg.port.

    Uses uvicorn's ``factory=True`` mode (the import string points at
    ``create_app``) so ``reload=True`` works — uvicorn needs an import
    string, not a pre-built app object, to spawn the reloader watcher.
    """
    from magi.channels.webui.app import create_app  # lazy: keeps FastAPI off EVE-without-webui

    host = cfg.host or "0.0.0.0"
    port = cfg.port or 42069
    logger.info(
        "webui channel starting",
        extra={"host": host, "port": port, "reload": cfg.reload},
    )
    # Pin the reload watcher to the magi package root.
    # Without this, uvicorn falls back to ``os.getcwd()`` —
    # which inside the dev container is ``/web`` (the Vite
    # web root, not the Python source tree). The watcher
    # silently no-ops against the wrong tree, so D.6 / D.7
    # edits never reached the running process and a manual
    # ``docker restart`` was needed to pick them up.
    reload_dirs = ["/workspace/magi"] if cfg.reload else None

    uvicorn.run(
        "magi.channels.webui.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=cfg.log_level.lower(),
        reload=cfg.reload,
        reload_dirs=reload_dirs,
    )


def _launch_telegram(cfg: NodeConfig) -> None:
    """Mount the Telegram channel: start a python-telegram-bot listener.

    C0 behaviour is the "first-touch" handler from
    ``magi.channels.telegram.bot``: anyone not in the admin
    list (an ``Employee`` row with ``role='admin'``) gets a
    reply with their own chat_id and a "contact admin" nudge.
    C3 will replace this with the real agent-loop dispatcher.

    No-op when no bot token has been saved (e.g. onboarding step 1 not
    yet done). The bot daemon thread restarts are not required to pick
    up new admins — the allowlist is re-read on every message.
    """
    state_dir = cfg.state_dir or "/workspace/memories"
    from magi.channels.telegram.bot import start_bot

    thread = start_bot(state_dir)
    if thread is None:
        logger.info(
            "telegram: bot token not saved yet — channel idle until onboarding completes",
            extra={"state_dir": state_dir},
        )
        return
    logger.info(
        "telegram channel running",
        extra={
            "employee_id": cfg.employee_id,
            "adam_url": cfg.adam_url,
            "state_dir": state_dir,
            "bot_thread": thread.name,
        },
    )


_LAUNCHERS = {
    "webui": _launch_webui,
    "telegram": _launch_telegram,
}


def _launch_channel(name: str, cfg: NodeConfig) -> None:
    launcher = _LAUNCHERS.get(name)
    if launcher is None:
        logger.error("no launcher registered for channel %r", name)
        return
    launcher(cfg)


# ----------------------------------------------------------------------
# utils
# ----------------------------------------------------------------------
def _json_default(value: object) -> object:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")