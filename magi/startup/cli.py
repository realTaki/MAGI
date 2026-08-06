"""Unified MAGI CLI — plan §18.

Single entry point for the operator-facing verbs:

- ``magi run``        — bootstrap + serve one MAGI in-process
- ``magi create``     — register a new MAGI under an existing MAGIS
- ``magi start``      — spawn detached subprocess for one MAGI
- ``magi stop``       — SIGTERM the subprocess
- ``magi restart``    — stop + start
- ``magi status``     — list local slots + their liveness

All verbs route through :mod:`magi.startup.config` /
:mod:`magi.startup.bootstrap` / :mod:`magi.startup.local`. There is no
Runtime / CLI / Kubernetes split — a single package owns startup.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional, Sequence

from magi.startup import local, webui
from magi.startup.config import StartupConfig
from magi.startup.context import StartupContext

logger = __import__("logging").getLogger("magi.startup.cli")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def _is_first_magi_or_none(config: StartupConfig) -> bool:
    """True when the local slot matches ``eva-000`` — that's the WebUI's slot."""
    return config.magi_name == DEFAULT_MAGI_NAME


# ----------------------------------------------------------------------
# Command handlers
# ----------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    """``magi run`` — bootstrap and serve this MAGI's runtime."""
    config = _config_from_args(args)
    # Run the async runtime composition in the main loop.
    from magi.startup.runtime import run_magi

    asyncio.run(run_magi(config))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """``magi create`` — register a new MAGI under an existing MAGIS."""
    config = _config_from_args(args)
    if config.is_first_magi:
        print(
            "error: `magi create` requires an existing MAGIS — "
            "set MAGIS_DATABASE_URL or run `magi run` first",
            file=sys.stderr,
        )
        return 2
    return local.create_magi(
        config=config,
        start=not args.no_start,
        port=args.port,
    )


def cmd_start(args: argparse.Namespace) -> int:
    """``magi start`` — spawn a detached subprocess for one MAGI."""
    config = _config_from_args(args)
    rc = local.start_magi(config=config, port=args.port)
    if rc == 0 and _is_first_magi_or_none(config):
        # First MAGI only — start the singleton WebUI.
        webui.ensure_webui_running(config=config, port=args.webui_port)
    return rc


def cmd_stop(args: argparse.Namespace) -> int:
    """``magi stop`` — SIGTERM one MAGI's subprocess + (if first) WebUI."""
    config = _config_from_args(args)
    rc = local.stop_magi(config=config, force=args.force)
    if _is_first_magi_or_none(config):
        webui.stop_webui(config=config, force=args.force)
    return rc


def cmd_restart(args: argparse.Namespace) -> int:
    """``magi restart`` — stop + start."""
    config = _config_from_args(args)
    return local.restart_magi(config=config, port=args.port)


def cmd_status(args: argparse.Namespace) -> int:
    """``magi status`` — list local slots + their liveness."""
    config = _config_from_args(args)
    rows = []
    for slot in local.list_slots(config.host_workspace_dir):
        sub_config = StartupConfig(
            host_workspace_dir=config.host_workspace_dir,
            magi_name=slot,
            magis_database_url=config.magis_database_url,
            magi_id=config.magi_id,
        )
        st = local.status_magi(config=sub_config)
        rows.append(
            [
                slot,
                str(st.pid) if st.pid else "-",
                "alive" if st.alive else "dead",
                st.pid_file,
            ]
        )
    if not rows:
        print("(no MAGI slots — run `magi run` first)")
        return 0
    _print_table(["name", "pid", "state", "pid_file"], rows)
    return 0


def cmd_webui(args: argparse.Namespace) -> int:
    """``magi webui`` — boot the singleton WebUI in-process (used by ``magi start``)."""
    # Same entry point as the legacy `magi webui` — uvicorn.run with the
    # control app. The detached subprocess wrapper is in :func:`webui.start_webui`.
    from magi.__main__ import run_webui as legacy_run_webui

    legacy_run_webui()
    return 0


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host-workspace-dir",
        default=None,
        help="operator host workspace (default: ~/.magi)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help=f"MAGI name (default: {DEFAULT_MAGI_NAME})",
    )
    parser.add_argument(
        "--magis",
        default=None,
        dest="magis_database_url",
        help="MAGIS database URL (omit ⇒ bootstrap first MAGIS)",
    )
    parser.add_argument(
        "--magi-id",
        default=None,
        dest="magi_id",
        help="MAGI identity when joining an existing MAGIS",
    )


def _config_from_args(args: argparse.Namespace) -> StartupConfig:
    defaults: dict[str, object] = {}
    if getattr(args, "host_workspace_dir", None):
        defaults["host_workspace_dir"] = args.host_workspace_dir
    if getattr(args, "name", None):
        defaults["magi_name"] = args.name
    if getattr(args, "magis_database_url", None):
        defaults["magis_database_url"] = args.magis_database_url
    if getattr(args, "magi_id", None):
        defaults["magi_id"] = args.magi_id
    return StartupConfig.from_cli(None, defaults=defaults)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magi",
        description="MAGI unified CLI — every startup verb lives here.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p = sub.add_parser("run", help="bootstrap + serve one MAGI in-process")
    _add_common_args(p)
    p.set_defaults(handler=cmd_run)

    # create
    p = sub.add_parser("create", help="register a new MAGI under an existing MAGIS")
    _add_common_args(p)
    p.add_argument("--start", action="store_true", help="spawn subprocess after create")
    p.add_argument("--no-start", action="store_true", help="skip subprocess spawn")
    p.add_argument("--port", type=int, default=local.DEFAULT_PORT)
    p.set_defaults(handler=cmd_create)

    # start
    p = sub.add_parser("start", help="spawn a detached MAGI subprocess")
    _add_common_args(p)
    p.add_argument("--port", type=int, default=local.DEFAULT_PORT)
    p.add_argument("--webui-port", type=int, default=webui.DEFAULT_WEBUI_PORT)
    p.set_defaults(handler=cmd_start)

    # stop
    p = sub.add_parser("stop", help="stop one MAGI subprocess (SIGTERM)")
    _add_common_args(p)
    p.add_argument("--force", action="store_true", help="SIGKILL immediately")
    p.set_defaults(handler=cmd_stop)

    # restart
    p = sub.add_parser("restart", help="stop + start one MAGI subprocess")
    _add_common_args(p)
    p.add_argument("--port", type=int, default=local.DEFAULT_PORT)
    p.set_defaults(handler=cmd_restart)

    # status
    p = sub.add_parser("status", help="list local MAGI slots")
    _add_common_args(p)
    p.set_defaults(handler=cmd_status)

    # webui
    p = sub.add_parser("webui", help="boot the singleton WebUI in-process")
    _add_common_args(p)
    p.set_defaults(handler=cmd_webui)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


__all__ = ["main", "build_parser", "StartupConfig", "StartupContext"]