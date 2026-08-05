"""``magi local start | status | stop | doctor | install-service | uninstall-service``.

Each MAGI is an independent OS process — the Local Profile never spawns child
processes.  ``magi local start`` bootstraps the workspace (first run only) and
then *becomes* the MAGI runtime in the current process.  ``magi local
install-service`` registers one systemd user unit per MAGI so every MAGI
starts, crashes, and restarts independently.
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

from magi.launcher.paths import (
    control_dir as _control_dir,
    control_secret_path,
    default_data_root,
    launcher_state_path,
)
from magi.launcher.platform import current_platform, open_browser
from magi.launcher.security import ensure_control_secret, reveal_control_secret

logger = logging.getLogger("magi.launcher.cli")

# ──────────────────────────────────────────────────────────────────────────── #
# helpers
# ──────────────────────────────────────────────────────────────────────────── #


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


def _bootstrap_once(data_root: Path) -> int:
    """Ensure workspace + MAGIS schema exist.  Returns the adam magic_id.

    Safe to call on every boot — the seed is idempotent.  If no MAGIC
    row exists yet (brand-new workspace), the seed creates the Genesis
    MAGIS + Adam MAGIC and returns its id.
    """
    from magi.launcher import bootstrap_local

    bus = bootstrap_local(data_root, initialise=True)

    from magi.bus.db.magis import init_magis_public_db
    init_magis_public_db(seed_root=True)

    magics = bus.magic.list_all_magic()
    if magics:
        return magics[0].id
    raise RuntimeError(
        "no MAGIC row found after seed — "
        "run `magi local start` once first to bootstrap."
    )


def _slug_from_name(name: str, magic_id: int) -> str:
    """Derive a directory-safe slug from a MAGIC display name.

    The first MAGI (id=1, seeded as ``"EVA-000"``) becomes
    ``eva-000``.  Subsequent MAGIs append their id to ``eva-`` padded
    to three digits (``eva-001``, ``eva-002``, …).

    Returns a lowercase, hyphen-separated slug with no spaces.
    """
    import re
    # First MAGI is always eva-000 regardless of display name.
    if magic_id == 1:
        return "eva-000"
    # Derive from name: lowercase, strip non-alphanumeric, replace
    # whitespace/underscore runs with a single hyphen.
    base = name.strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = "eva"
    return f"{base}-{magic_id:03d}"


def _magic_name_by_id(data_root: Path, magic_id: int) -> str:
    """Look up the MAGIC display name from the MAGIS database."""
    from magi.launcher import bootstrap_local
    bus = bootstrap_local(data_root)
    magic = bus.magic.get_magic(magic_id)
    return magic.name if magic and magic.name else "eva"


def _resolve_runtime(data_root: Path, name: str | None) -> tuple[int, str]:
    """Return ``(magic_id, slug)`` for the named MAGIC, or the first one.

    Slots are directory-safe slugs (e.g. ``eva-000``, ``eva-001``).
    ``name`` matches against the slug or the MAGIC display name.
    """
    magic_dir = data_root / "MAGIC"

    # First run: no slots yet.  The seed created Adam as id=1.
    if not magic_dir.exists() or not any(
        p.is_dir() for p in magic_dir.iterdir()
    ):
        # Default: first MAGI → eva-000
        if not name or name.strip().lower() in ("adam", "eva-00", "eva-000", "1"):
            return 1, "eva-000"
        raise SystemExit(
            f"--name={name!r} requested but no MAGIC slots exist yet. "
            "Run `magi local start` once first (defaults to first MAGI)."
        )

    slots = sorted(
        p.name for p in magic_dir.iterdir() if p.is_dir()
    )

    if name:
        name_lower = name.strip().lower()
        # Direct slug match
        for slot in slots:
            if slot.lower() == name_lower:
                # We need magic_id — read from MAGIS DB or infer from slug
                return _magic_id_for_slug(data_root, slot), slot
        raise SystemExit(
            f"No MAGIC slot matches {name!r}.  Known slots: {', '.join(slots)}"
        )

    if len(slots) == 1:
        return _magic_id_for_slug(data_root, slots[0]), slots[0]

    raise SystemExit(
        f"Multiple MAGIC slots exist ({', '.join(slots)}).  "
        "Pick one with `magi local start --name <slug>`."
    )


def _magic_id_for_slug(data_root: Path, slug: str) -> int:
    """Reverse-map a slug back to the MAGIC id from the MAGIS database."""
    from magi.launcher import bootstrap_local
    bus = bootstrap_local(data_root)
    magics = bus.magic.list_all_magic()
    for m in magics:
        if _slug_from_name(m.name or "eva", m.id) == slug:
            return m.id
    # Fallback: if the first slug is eva-000, it's id=1
    return 1


def _exec_runtime(
    data_root: Path,
    magic_id: int,
    slug: str,
    port: int,
    *,
    reload: bool = True,
) -> None:
    """Replace the current process with ``magi runtime`` for one MAGI.

    Sets env vars so :mod:`magi.launcher.paths` resolves state_dir /
    workspace_dir to the correct per-MAGI slot.  Never returns.

    ``reload`` defaults to True so the Local Profile matches the
    k8s-dev experience — every source edit a developer saves is
    picked up by the running MAGI in seconds, no restart.  Pass
    ``reload=False`` for production runs (``magi local start --no-reload``).
    """
    ws_root = data_root / "MAGIC" / slug / "workspace"
    magis_db = data_root / "MAGIS" / "1-genesis" / "magis.db"

    env = os.environ.copy()
    env.update({
        "MAGI_DATA_ROOT": str(data_root),
        "MAGI_WORKSPACE_DIR": str(ws_root),
        "MAGI_RUNTIME_ID": str(magic_id),
        "MAGI_RUNTIME_SLUG": slug,
        "MAGIS_DATABASE_URL": f"sqlite:///{magis_db}",
        "MAGI_PORT": str(port),
        "MAGI_RELOAD": "1" if reload else "0",
    })

    # execv replaces the current process image — no fork, no child.
    argv = [sys.executable, "-m", "magi", "runtime", "--host", "127.0.0.1", "--port", str(port)]
    logger.info(
        "replacing process with magi runtime: %s",
        " ".join(argv),
        extra={"magic_id": magic_id, "slug": slug, "port": port, "reload": reload},
    )
    os.execve(sys.executable, argv, env)


# ──────────────────────────────────────────────────────────────────────────── #
# commands
# ──────────────────────────────────────────────────────────────────────────── #


def cmd_start(args: argparse.Namespace) -> int:
    """``magi local start`` — bootstrap once, then *become* the MAGI runtime.

    First boot seeds Genesis + Adam into the per-MAGIS SQLite.
    Subsequent calls skip the seed and go straight to runtime mode.

    The process replaces itself with ``magi runtime`` via ``execve``,
    so the current shell / terminal owns the MAGI's stdout/stderr and
    Ctrl-C stops it cleanly.  When run via systemd, the unit is the
    MAGI itself — no launcher process, no subprocess tree.
    """
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()

    # ── control-plane bootstrap (first run only) ──
    control = _control_dir(data_root)
    secret_path = control_secret_path(control)
    ensure_control_secret(secret_path)
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

    # Idempotent seed — creates Genesis + Adam on first run.
    magic_id = _bootstrap_once(data_root)

    # ── resolve which MAGI to run ──
    magic_id, slug = _resolve_runtime(data_root, getattr(args, "name", None))

    # ── ensure the per-MAGI workspace slot exists ──
    ws_root = data_root / "MAGIC" / slug / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    from magi.launcher.paths import bootstrap_workspace
    bootstrap_workspace(ws_root)

    # ── port ──
    port = int(getattr(args, "port", 0) or os.environ.get("MAGI_PORT", "42069"))

    if not getattr(args, "no_open", False):
        base_url = f"http://127.0.0.1:{port}"
        open_browser(base_url)

    _exec_runtime(data_root, magic_id, slug, port)
    return 0  # unreachable — execve replaces the process


def cmd_status(args: argparse.Namespace) -> int:
    """``magi local status`` — list MAGIC slots and their process state."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    magic_dir = data_root / "MAGIC"
    if not magic_dir.exists():
        print("(no MAGIC slots — run `magi local start` first)")
        return 0

    slots = sorted(
        p.name for p in magic_dir.iterdir() if p.is_dir()
    )
    if not slots:
        print("(no MAGIC slots)")
        return 0

    rows: list[list[str]] = []
    for slug in slots:
        mid = str(_magic_id_for_slug(data_root, slug))
        ws = magic_dir / slug / "workspace"
        db = ws / "memories" / "magi.db"
        rows.append([
            mid,
            slug,
            "exists" if db.exists() else "no db",
            str(ws) if ws.exists() else "(missing)",
        ])
    _print_table(["id", "slug", "state", "workspace"], rows)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """``magi local stop`` — send SIGTERM to every MAGI runtime.

    Reads PIDs from the control registry and signals them.  This is a
    convenience for the operator; systemd-managed units should use
    ``systemctl --user stop magi-<slug>.service`` instead.
    """
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    from magi.launcher import bootstrap_local
    bus = bootstrap_local(data_root)
    if bus.control_registry is None:
        print("error: control registry unavailable", file=sys.stderr)
        return 2
    import signal
    runtimes = bus.control_registry.list_runtimes()
    for r in runtimes:
        if r.pid is not None:
            try:
                os.kill(r.pid, signal.SIGTERM)
                print(f"sent SIGTERM to runtime {r.runtime_id} (pid {r.pid})")
            except ProcessLookupError:
                print(f"runtime {r.runtime_id} (pid {r.pid}) already gone")
            except Exception as exc:
                print(f"failed to stop runtime {r.runtime_id}: {exc}", file=sys.stderr)
        bus.control_registry.record_stop(r.runtime_id)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """``magi local doctor`` — surface workspace + control-plane state."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    control = _control_dir(data_root)
    magic_dir = data_root / "MAGIC"
    slots = sorted(
        p.name for p in magic_dir.iterdir() if p.is_dir()
    ) if magic_dir.exists() else []

    print(json.dumps({
        "data_root": str(data_root),
        "platform": current_platform(),
        "paths": {
            "control": str(control),
            "magis": str(data_root / "MAGIS" / "1-genesis"),
            "magic_slots": slots,
        },
        "control_db_exists": (control / "local-registry.db").exists(),
        "secret_exists": control_secret_path(control).exists(),
        "launcher_state_exists": launcher_state_path(control).exists(),
    }, indent=2))
    return 0


# ──────────────────────────────────────────────────────────────────────────── #
# systemd service registration (Linux only)
# ──────────────────────────────────────────────────────────────────────────── #

_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
_SERVICE_TEMPLATE = (
    Path(__file__).resolve().parent.parent.parent
    / "deploy" / "local" / "service" / "magi@.service"
)


_SERVICE_TEMPLATE_CONTENT = """\
[Unit]
Description=MAGI runtime — {slug} (magic_id={magic_id})
Documentation=https://github.com/realTaki/MAGI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=MAGI_DATA_ROOT={data_root}
Environment=MAGI_RUNTIME_ID={magic_id}
Environment=MAGI_RUNTIME_SLUG={slug}
Environment=MAGI_PORT={port}
Environment=MAGIS_DATABASE_URL=sqlite:///{magis_db}
ExecStart={magi_bin} runtime --host 127.0.0.1 --port {port}
Restart=on-failure
RestartSec=5
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={data_root}
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""


def _magi_executable() -> str:
    found = shutil.which("magi")
    if found:
        return found
    return f"{sys.executable} -m magi"


def _list_magic_slots(data_root: Path) -> list[tuple[int, str]]:
    """Return ``[(magic_id, slug), ...]`` from the MAGIC directory.

    Slugs are directory-safe names like ``eva-000``, ``eva-001``.
    The ``magic_id`` is reverse-mapped from the MAGIS database.
    """
    magic_dir = data_root / "MAGIC"
    if not magic_dir.exists():
        return []
    slots: list[tuple[int, str]] = []
    for entry in sorted(magic_dir.iterdir()):
        if not entry.is_dir():
            continue
        slug = entry.name
        mid = _magic_id_for_slug(data_root, slug)
        slots.append((mid, slug))
    return slots


def cmd_install_service(args: argparse.Namespace) -> int:
    """``magi local install-service`` — register one systemd unit per MAGI."""
    if current_platform() != "linux":
        print(
            f"error: install-service is Linux-only (current platform: {current_platform()}). "
            "On macOS, see deploy/local/service/magi.plist.example.  "
            "On Windows, see deploy/local/service/magi-task.xml.",
            file=sys.stderr,
        )
        return 2

    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    magi_bin = _magi_executable()
    magis_db = data_root / "MAGIS" / "1-genesis" / "magis.db"

    slots = _list_magic_slots(data_root)
    if not slots:
        # First-run: bootstrap so there is at least the Adam slot.
        _bootstrap_once(data_root)
        slots = _list_magic_slots(data_root)
    if not slots:
        print("error: no MAGIC slots found after bootstrap", file=sys.stderr)
        return 2

    if not shutil.which("systemctl"):
        print(
            "error: systemctl not found on PATH. "
            "Install systemd or run `magi local start` manually.",
            file=sys.stderr,
        )
        return 2

    target_dir = _SYSTEMD_USER_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # Assign ports: Adam gets 42069, subsequent EVAs get 42070+
    base_port = 42069
    registered: list[dict] = []

    for idx, (magic_id, slug) in enumerate(slots):
        port = base_port + idx
        unit_name = f"magi-{slug}.service"
        target = target_dir / unit_name

        body = _SERVICE_TEMPLATE_CONTENT.format(
            slug=slug,
            magic_id=magic_id,
            data_root=data_root,
            port=port,
            magis_db=magis_db,
            magi_bin=magi_bin,
        )
        target.write_text(body, encoding="utf-8")
        target.chmod(0o644)
        registered.append({"unit": unit_name, "magic_id": magic_id, "slug": slug, "port": port})
        print(f"wrote {target}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for r in registered:
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", r["unit"]],
            check=True,
        )
        print(f"enabled + started {r['unit']}")

    print(json.dumps(
        {"ok": True, "units": registered, "hint": "systemctl --user list-units 'magi-*'"},
        indent=2,
    ))
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    """``magi local uninstall-service`` — remove all magi-*.service units."""
    if current_platform() != "linux":
        print(
            f"error: uninstall-service is Linux-only (current platform: {current_platform()}).",
            file=sys.stderr,
        )
        return 2

    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    slots = _list_magic_slots(data_root)

    target_dir = _SYSTEMD_USER_DIR
    removed = []
    for magic_id, slug in slots:
        unit_name = f"magi-{slug}.service"
        target = target_dir / unit_name
        if shutil.which("systemctl") and target.exists():
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", unit_name],
                check=False,
            )
        if target.exists():
            target.unlink()
            removed.append(unit_name)

    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(json.dumps({"ok": True, "removed": removed}, indent=2))
    return 0


# ──────────────────────────────────────────────────────────────────────────── #
# parser
# ──────────────────────────────────────────────────────────────────────────── #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magi local",
        description="MAGI Local Profile — each MAGI is an independent process.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p = sub.add_parser("start", help="bootstrap once, then become the MAGI runtime")
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.add_argument("--name", "-n", default=None, help="MAGI slug or <id>-<slug> to run")
    p.add_argument("--port", "-p", type=int, default=None, help="HTTP port (default: 42069)")
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p.set_defaults(handler=cmd_start)

    # status
    p = sub.add_parser("status", help="list MAGIC slots")
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.set_defaults(handler=cmd_status)

    # stop
    p = sub.add_parser("stop", help="SIGTERM every MAGI runtime")
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.set_defaults(handler=cmd_stop)

    # doctor
    p = sub.add_parser("doctor", help="print diagnostic state")
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.set_defaults(handler=cmd_doctor)

    # install-service
    p = sub.add_parser("install-service", help="register one systemd unit per MAGI")
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.set_defaults(handler=cmd_install_service)

    # uninstall-service
    p = sub.add_parser("uninstall-service", help="remove all magi-*.service units")
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.set_defaults(handler=cmd_uninstall_service)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


__all__ = ["main", "build_parser"]
