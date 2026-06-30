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
        state_dir = os.environ.get("MAGI_STATE_DIR", "/workspace/state")

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

    if not cfg.channels:
        logger.warning("no channels enabled (MAGI_CHANNELS is empty); exiting")
        return

    for channel in cfg.channels:
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

    state_dir = cfg.state_dir or "/workspace/state"
    from magi.runtime.state import init_sqlite

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
    uvicorn.run(
        "magi.channels.webui.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=cfg.log_level.lower(),
        reload=cfg.reload,
    )


def _launch_telegram(cfg: NodeConfig) -> None:
    """Mount the Telegram channel. C0 stub; real wiring lands in C3."""
    logger.info(
        "telegram channel stub (C0); C3 will mount python-telegram-bot",
        extra={
            "employee_id": cfg.employee_id,
            "adam_url": cfg.adam_url,
            "bot_token_set": cfg.bot_token_set,
            "state_dir": cfg.state_dir,
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