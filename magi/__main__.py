"""MAGI executable entry point.

Per MAGI_UNIFIED_STARTUP_REFACTOR_PLAN_V2, every startup verb is routed
through :mod:`magi.startup`. This module is intentionally thin — it
only parses the legacy ``magi [runtime|webui|cli] [--check]`` form and
forwards the verb to the corresponding :func:`magi.startup.cli`
command.

The legacy service names keep their historical semantics:

- ``runtime``  → :func:`magi.startup.cli.cmd_run` (was ``run``)
- ``webui``    → :func:`magi.startup.cli.cmd_webui`
- ``cli``      → :func:`magi.startup.cli.main` (start | status | stop | …)

The legacy ``run()`` body has been migrated to
:mod:`magi.startup.runtime.run_magi`; ``run_webui`` delegates to
``magi.startup.cli.cmd_webui`` which boots uvicorn on the control app.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass

import uvicorn

from magi import __version__
from magi.startup.config import (
    DEFAULT_LOG_LEVEL,
    RUNTIME_HOST,
    RUNTIME_PORT,
    WEBUI_HOST,
    WEBUI_PORT,
)

logger = logging.getLogger("magi")

# Internal host + port constants live in :mod:`magi.startup.config`
# (plan §5 / §15 / §21).  Aliases below keep the legacy module-local
# name shape so the ``--check`` payload still reads naturally.
_RUNTIME_HOST: str = RUNTIME_HOST
_RUNTIME_PORT: int = RUNTIME_PORT
_WEBUI_HOST: str = WEBUI_HOST
_WEBUI_PORT: int = WEBUI_PORT
_DEFAULT_LOG_LEVEL: str = DEFAULT_LOG_LEVEL


# ----------------------------------------------------------------------
# Legacy ``--check`` payload (NodeConfig.from_env)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class NodeConfig:
    """Minimal config for a MAGI node (legacy ``--check`` payload)."""

    state_dir: str | None = None
    host: str = _RUNTIME_HOST
    port: int = _RUNTIME_PORT
    reload: bool = False
    log_level: str = _DEFAULT_LOG_LEVEL
    runtime_id: str | None = None
    is_genesis: bool = False

    @classmethod
    def from_env(cls) -> "NodeConfig":
        # Reading MAGI_RUNTIME_ID stays for backward compatibility with
        # container readiness probes; new code should use MAGI_ID.
        import os

        runtime_id_raw = os.environ.get("MAGI_RUNTIME_ID", "").strip()
        if runtime_id_raw:
            if not runtime_id_raw.isdigit():
                raise ValueError("MAGI_RUNTIME_ID must be an integer magic_id")
            runtime_id = runtime_id_raw
            is_genesis = False
        else:
            runtime_id = None
            is_genesis = True

        # Log level honours DB setting if reachable.
        log_level = DEFAULT_LOG_LEVEL
        try:
            from magi.startup.paths import resolve_state_dir as _state_dir
            from magi.bus.db.engine import build_local_factory
            from magi.bus.library.local.settingBook import SettingBook

            db_level = SettingBook(build_local_factory(str(_state_dir()))).get(
                key="system.log_level"
            )
            if db_level and db_level in ("debug", "info", "warning", "error"):
                log_level = db_level
        except Exception:
            pass

        return cls(
            state_dir=None,
            host=RUNTIME_HOST,
            port=RUNTIME_PORT,
            reload=os.environ.get("MAGI_DEV_RELOAD") == "1",
            log_level=log_level,
            runtime_id=runtime_id,
            is_genesis=is_genesis,
        )


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
    """Legacy entry — ``magi runtime`` and the default command.

    Resolves a fresh :class:`StartupConfig` from the process environment
    and delegates to :func:`magi.startup.runtime.run_magi` so all runtime
    composition lives in :mod:`magi.startup.runtime`.
    """
    import asyncio

    from magi.startup.config import StartupConfig
    from magi.startup.runtime import run_magi

    config = StartupConfig.from_env()
    asyncio.run(run_magi(config))


def run_webui() -> None:
    """Legacy entry — ``magi webui`` boots the singleton WebUI in-process.

    WebUI host / port are fixed by plan §15 / §21 — operators cannot
    override them through the legacy ``magi webui`` form.
    """
    import os
    from pathlib import Path

    logging.basicConfig(
        level=DEFAULT_LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # The singleton WebUI serves the first-run onboarding and auth routes,
    # which access the public BUS facade.  It intentionally does not start
    # workers; the selected MAGI runtime owns those.  Both processes point at
    # the same durable local state directory.
    from magi.bus import bootstrap_bus
    from magi.channels.api.app import create_control_app
    from magi.startup.paths import resolve_workspace_dir

    state_dir = Path(resolve_workspace_dir()) / "memories"
    state_dir.mkdir(parents=True, exist_ok=True)
    app = create_control_app(bus=bootstrap_bus(state_dir=str(state_dir)))
    uvicorn.run(
        app,
        host=str(os.environ.get("MAGI_WEBUI_HOST") or WEBUI_HOST),
        port=int(os.environ.get("MAGI_WEBUI_PORT") or WEBUI_PORT),
        log_level=DEFAULT_LOG_LEVEL,
        reload=False,
        reload_dirs=None,
    )


# ----------------------------------------------------------------------
# top-level argparse
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the MAGI command-line entry point."""
    parser = argparse.ArgumentParser(
        prog="magi",
        description=(
            "MAGI unified startup.  The default command boots the "
            "Runtime in-process; explicit verbs route through "
            "`magi.startup.cli`."
        ),
    )
    parser.add_argument("--version", action="version", version=f"magi {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print resolved config as JSON and exit.",
    )
    parser.add_argument(
        "service",
        nargs="*",
        help=(
            "service role: runtime (default), webui, or "
            "`cli` (followed by start|status|stop|restart|run|create)."
        ),
    )
    args, extras = parser.parse_known_args(argv)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not args.service:
        args.service = ["runtime"]
    if args.service[0] not in ("runtime", "webui", "cli"):
        parser.error(
            f"unknown service {args.service[0]!r} "
            "(expected runtime | webui | cli)"
        )
    if args.check:
        return check()
    if args.service[0] == "webui":
        run_webui()
        return 0
    if args.service[0] == "cli":
        return _run_cli(argv)
    run()
    return 0


def _run_cli(argv: list[str] | None) -> int:
    """Dispatch ``magi cli <verb>`` to the unified startup CLI.

    New verbs (``start|stop|restart|status|run|create|webui``) live in
    :mod:`magi.startup.cli`.
    """
    from magi.startup.cli import main as startup_main

    rest = list(argv or [])
    if rest and rest[0] == "cli":
        rest = rest[1:]

    if rest and rest[0] in {"run", "create", "start", "stop", "restart", "status", "webui"}:
        return int(startup_main(rest))

    # Unknown verb — delegate to the unified CLI which will show usage.
    return int(startup_main(rest))


def _json_default(value: object) -> object:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


if __name__ == "__main__":
    sys.exit(main())
