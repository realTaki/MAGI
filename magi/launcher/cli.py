"""``magi local start | status | stop | doctor | install-service | uninstall-service``.

The Local Profile launcher is intentionally tiny:

- ``start``  — provision the control-plane registry, persist the
              launcher-issued control secret, idempotently start the
              Adam runtime, and open the browser tab.
- ``status`` — list every runtime the control registry knows about,
              flag stale rows, summarize port allocation.
- ``stop``   — stop every runtime, do **not** release ports (per
              plan §7.4 — only ``delete`` releases).
- ``doctor`` — surface the control-registry state for an operator
              investigating "why isn't my local MAGI talking to me?".
- ``install-service``   — write a systemd user unit that runs
              ``magi local start`` on boot (Linux only).
- ``uninstall-service`` — remove the systemd user unit.

The CLI is a thin wrapper over the BUS services built during Phases
3-5 — never a parallel set of bootstrappers.  When ``magi local``
isn't the right verb (e.g. for inspecting a single runtime's
``/health``), the operator uses ``bus.control_registry.list_runtimes()``
directly via the Python REPL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from magi.bus.contracts.lifecycle import RuntimeSpec
from magi.bus.db.control.models import RuntimeDesiredState, RuntimeObservedState
from magi.launcher import bootstrap_local
from magi.launcher.paths import (
    control_dir as _control_dir,
    control_secret_path,
    default_data_root,
    launcher_state_path,
)
from magi.launcher.platform import current_platform, open_browser
from magi.launcher.security import ensure_control_secret, reveal_control_secret

logger = logging.getLogger("magi.launcher.cli")


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def cmd_start(args: argparse.Namespace) -> int:
    """``magi local start``: provision + idempotent start + open browser."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    if not args.no_open:
        logger.info("local launcher start", extra={"data_root": str(data_root)})

    control = _control_dir(data_root)
    secret_path = control_secret_path(control)
    secret = ensure_control_secret(secret_path)
    state_path = launcher_state_path(control)
    state_path.write_text(
        json.dumps(
            {
                "data_root": str(data_root),
                "platform": current_platform(),
                "secret_path": str(secret_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    bus = bootstrap_local(data_root, initialise=True, initialise_control=True)
    if bus.control_registry is None:
        print("error: control registry was not initialised", file=sys.stderr)
        return 2

    # Local Profile seeds the public MAGIS schema (Adam + Genesis MAGIS)
    # directly into the Local SQLite engine — the same ``seed_root=True``
    # path ``magi runtime`` uses in the container. Without this, the
    # private SQLite has the runtime tables but the MAGIS engine has
    # nothing and ``list_all_magic`` raises ``no such table: magic``.
    from magi.bus.db.magis import init_magis_public_db

    init_magis_public_db(seed_root=True)

    # Phase 6 baseline: ensure the Adam runtime exists.
    magic_id = _ensure_adam(bus)
    bus.control_registry.upsert_desired_state(
        magic_id, "local_process", RuntimeDesiredState.STARTED
    )

    # Boot the runtime via the orchestrator backend.
    backend_kind = bus.runtime.backend.kind
    if backend_kind != "local_process":
        print(
            f"warning: backend is {backend_kind!r}; Local launcher requires "
            f"MAGI_BACKEND=local_process. The Adam will not start.",
            file=sys.stderr,
        )
        return 1

    result = bus.runtime.start(
        RuntimeSpec(magic_id=magic_id, name="adam")
    )
    print(json.dumps(
        {
            "ok": True,
            "data_root": str(data_root),
            "magic_id": magic_id,
            "backend_kind": result.backend_kind,
            "endpoint": result.endpoint.base_url if result.endpoint else None,
            "secret": reveal_control_secret(secret_path) if args.print_secret else None,
        },
        indent=2,
        default=str,
    ))
    if not args.no_open and result.endpoint:
        open_browser(result.endpoint.base_url)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """``magi local status``: list control-registry rows."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    bus = bootstrap_local(data_root, initialise_control=True)
    if bus.control_registry is None:
        print("error: control registry unavailable", file=sys.stderr)
        return 2
    runtimes = bus.control_registry.list_runtimes()
    stale = {r.runtime_id for r in bus.control_registry.list_stale()}
    rows: list[list[str]] = []
    for r in runtimes:
        rows.append([
            str(r.runtime_id),
            r.backend_kind,
            r.desired_state.value,
            r.observed_state.value,
            str(r.port or "-"),
            str(r.pid or "-"),
            str(r.base_url or "-"),
            "stale" if r.runtime_id in stale else "ok",
        ])
    if rows:
        _print_table(
            ["id", "backend", "desired", "observed", "port", "pid", "base_url", "health"],
            rows,
        )
    else:
        print("(no runtimes registered)")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """``magi local stop``: stop every runtime; do not release ports."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    bus = bootstrap_local(data_root, initialise_control=True)
    if bus.control_registry is None:
        print("error: control registry unavailable", file=sys.stderr)
        return 2
    runtimes = bus.control_registry.list_runtimes()
    for r in runtimes:
        try:
            bus.runtime.stop(RuntimeSpec(magic_id=r.runtime_id, name=r.backend_ref))
            print(f"stopped runtime {r.runtime_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"failed to stop runtime {r.runtime_id}: {exc}", file=sys.stderr)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """``magi local doctor``: surface health + diagnostics for the operator."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    print(json.dumps({
        "data_root": str(data_root),
        "platform": current_platform(),
        "paths": {
            "control": str(_control_dir(data_root)),
            "magis": str(data_root / "MAGIS" / "local"),
        },
        "control_db_exists": (_control_dir(data_root) / "local-registry.db").exists(),
        "secret_exists": control_secret_path(_control_dir(data_root)).exists(),
        "launcher_state_exists": launcher_state_path(_control_dir(data_root)).exists(),
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Service registration (Linux systemd user unit)
# ---------------------------------------------------------------------------
#
# The Local Profile in production runs as a normal user process started
# by the operator — but on Linux we expose two extra verbs so the same
# deployment can be promoted to a "starts on login, restarts on crash"
# service without taking on the container / k8s machinery.
#
# Only Linux is supported. macOS uses launchd (we document the plist
# in deploy/local/service/magi.plist.example but do not auto-install it
# from Python). Windows uses Task Scheduler XML — see
# deploy/local/README.md.
#
# The wallpaper is shipped at deploy/local/service/magi.service.  The
# CLI copies it into ``~/.config/systemd/user/magi.service`` and runs
# ``systemctl --user daemon-reload`` plus ``enable --now``.

_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
_SYSTEMD_SERVICE_NAME = "magi.service"
_SERVICE_TEMPLATE_CANDIDATES = (
    # Source checkout (development)
    Path(__file__).resolve().parent.parent.parent / "deploy" / "local" / "service" / "magi.service",
    # Installed wheel (pip-installed magi)
    Path(__file__).resolve().parent.parent / "share" / "magi" / "local" / "magi.service",
)


def _resolve_service_template() -> Optional[Path]:
    for candidate in _SERVICE_TEMPLATE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _magi_executable() -> str:
    """Best-effort path to the ``magi`` binary used by the unit."""
    found = shutil.which("magi")
    if found:
        return found
    # Fallback: ``python -m magi`` — works for editable installs.
    return f"{sys.executable} -m magi"


def cmd_install_service(args: argparse.Namespace) -> int:
    """``magi local install-service``: register systemd user unit (Linux only)."""
    if current_platform() != "linux":
        print(
            f"error: install-service is Linux-only (current platform: {current_platform()}). "
            "On macOS, copy deploy/local/service/magi.plist.example to "
            "~/Library/LaunchAgents/com.magi.local.plist and run "
            "`launchctl load -w <plist>`.  On Windows, import the XML in "
            "deploy/local/service/magi-task.xml via Task Scheduler.",
            file=sys.stderr,
        )
        return 2

    template = _resolve_service_template()
    if template is None:
        print(
            "error: cannot locate deploy/local/service/magi.service template. "
            "Reinstall magi from the source checkout.",
            file=sys.stderr,
        )
        return 2

    target_dir = _SYSTEMD_USER_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _SYSTEMD_SERVICE_NAME

    body = template.read_text(encoding="utf-8")
    # Substitute the actual magi binary path so the unit does not depend
    # on PATH being set correctly when systemd starts the user session.
    magi_path = _magi_executable()
    body = body.replace("__MAGI_BIN__", magi_path)
    target.write_text(body, encoding="utf-8")
    target.chmod(0o644)

    if not shutil.which("systemctl"):
        print(
            "error: systemctl not found on PATH. Install systemd or run "
            "`magi local start` manually as a foreground process.",
            file=sys.stderr,
        )
        return 2

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", _SYSTEMD_SERVICE_NAME], check=True)
    print(
        json.dumps(
            {
                "ok": True,
                "unit": str(target),
                "exec": magi_path,
                "hint": "systemctl --user status magi.service",
            },
            indent=2,
        )
    )
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    """``magi local uninstall-service``: remove systemd user unit (Linux only)."""
    if current_platform() != "linux":
        print(
            f"error: uninstall-service is Linux-only (current platform: {current_platform()}).",
            file=sys.stderr,
        )
        return 2

    target = _SYSTEMD_USER_DIR / _SYSTEMD_SERVICE_NAME
    if shutil.which("systemctl") and target.exists():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", _SYSTEMD_SERVICE_NAME],
            check=False,
        )
    if target.exists():
        target.unlink()
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(json.dumps({"ok": True, "removed": str(target)}, indent=2))
    return 0


def _ensure_adam(bus) -> int:
    """Ensure exactly one Adam MAGIC exists; return its id.

    Falls through to :func:`magi.bus.services.magic.MagicService.list`
    which already keeps a single-root invariant for the seeded
    Genesis tree (Phase 0 baseline).
    """
    from magi.bus.services.magic import MagicService

    svc: MagicService = bus.magic
    magics = svc.list_all_magic()
    if magics:
        return magics[0].id
    raise RuntimeError(
        "no Adam MAGIC found — run `python -m magi runtime` once first to seed."
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the ``magi local`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="magi local",
        description="MAGI Local Profile launcher (plan §12 Phase 6).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, help_text in (
        ("start", cmd_start, "provision + start the local Adam runtime"),
        ("status", cmd_status, "list registered runtimes from the control registry"),
        ("stop", cmd_stop, "stop every registered runtime"),
        ("doctor", cmd_doctor, "print diagnostic state for operator investigation"),
        ("install-service", cmd_install_service, "register systemd user unit (Linux only)"),
        ("uninstall-service", cmd_uninstall_service, "remove systemd user unit (Linux only)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--data-dir",
            default=None,
            help="override the OS-specific data root",
        )
        if name == "start":
            p.add_argument(
                "--no-open", action="store_true", help="don't open the browser"
            )
            p.add_argument(
                "--print-secret", action="store_true", help="echo the control secret"
            )
        p.set_defaults(handler=fn)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


__all__ = ["main", "build_parser"]
