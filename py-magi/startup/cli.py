"""The explicit MAGI provisioning and lifecycle command line interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from startup import local
from startup.config import DEFAULT_MAGI_NAME, StartupConfig
from startup.paths import resolve_runtime_state_path
from startup.provision import create_node, init_first_magi


def _config(args: argparse.Namespace) -> StartupConfig:
    return StartupConfig.from_cli(
        host_workspace_dir=getattr(args, "host_workspace_dir", None),
        magi_name=getattr(args, "name", None),
        magis_database_url=getattr(args, "magis_database_url", None),
        magi_id=getattr(args, "magi_id", None),
        magis_name=getattr(args, "magis_name", None),
    )


def cmd_init(args: argparse.Namespace) -> int:
    spec = init_first_magi(_config(args))
    print(f"initialized {spec.magi_name} (MAGI_ID={spec.magi_id}, port={spec.runtime_port})")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Start a usable local MAGI node in one idempotent command.

    A missing first-node runtime specification means this is a first launch,
    so provisioning is performed before the process is started.  Existing
    state is never recreated: an invalid or retired workspace still fails via
    the normal lifecycle commands rather than being silently replaced.
    The operator UI is not started here.
    """
    config = _config(args)
    if not resolve_runtime_state_path(config.workspace_dir).exists():
        spec = init_first_magi(config)
        print(f"initialized {spec.magi_name} (MAGI_ID={spec.magi_id})")

    node_status = local.status_magi(config=config)
    if node_status.alive:
        print(f"MAGI {config.magi_name!r} is already running (pid={node_status.pid})")
    elif local.start_magi(config=config) != 0:
        return 1
    else:
        print(f"MAGI {config.magi_name!r} started")
    return 0


def cmd_node_create(args: argparse.Namespace) -> int:
    spec = create_node(_config(args))
    print(f"created {spec.magi_name} (MAGI_ID={spec.magi_id}, port={spec.runtime_port})")
    return 0


def cmd_node_run(args: argparse.Namespace) -> int:
    config = _config(args)
    if not args.foreground:
        return local.start_magi(config=config)
    from startup.runtime import run_magi

    run_magi(config)
    return 0


def cmd_node_stop(args: argparse.Namespace) -> int:
    return local.stop_magi(config=_config(args), force=args.force)


def cmd_node_restart(args: argparse.Namespace) -> int:
    return local.restart_magi(config=_config(args))


def cmd_node_status(args: argparse.Namespace) -> int:
    status = local.status_magi(config=_config(args))
    print(
        f"{status.magi_name}\t{status.pid or '-'}\t{'alive' if status.alive else 'dead'}\t{status.pid_file}"
    )
    return 0


def _common(
    parser: argparse.ArgumentParser,
    *,
    name_default: str | None = DEFAULT_MAGI_NAME,
    name_required: bool = False,
) -> None:
    parser.add_argument("--host-workspace-dir")
    parser.add_argument("--name", default=name_default, required=name_required)
    parser.add_argument("--magis", dest="magis_database_url")
    parser.add_argument("--magis-name", help="MAGIS storage name when using local SQLite")
    parser.add_argument("--magi-id")


def _lifecycle_parser(
    parent: argparse._SubParsersAction,
    name: str,
    handler,
    *,
    foreground: bool = False,
    force: bool = False,
) -> None:
    parser = parent.add_parser(name)
    _common(parser)
    if foreground:
        parser.add_argument("--foreground", action="store_true", help="run in this process")
    if force:
        parser.add_argument("--force", action="store_true")
    parser.set_defaults(handler=handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magi", description="MAGI provisioning and runtime lifecycle"
    )
    root = parser.add_subparsers(dest="command", required=True)

    start = root.add_parser("start", help="initialize (when needed) and start a local MAGI node")
    _common(start)
    start.set_defaults(handler=cmd_start)

    init = root.add_parser("init", help="provision Genesis and eva-000")
    _common(init)
    init.set_defaults(handler=cmd_init)

    node = root.add_parser("node", help="manage provisioned MAGI nodes")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    create = node_sub.add_parser("create", help="register and provision an EVA")
    _common(create, name_default=None, name_required=True)
    create.set_defaults(handler=cmd_node_create)
    _lifecycle_parser(node_sub, "run", cmd_node_run, foreground=True)
    _lifecycle_parser(node_sub, "stop", cmd_node_stop, force=True)
    _lifecycle_parser(node_sub, "restart", cmd_node_restart)
    _lifecycle_parser(node_sub, "status", cmd_node_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


__all__ = ["build_parser", "main"]
