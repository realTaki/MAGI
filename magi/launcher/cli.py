"""``magi cli start | status | stop | doctor | install-service | uninstall-service``.

Each MAGI is an independent OS process.  ``magi cli start`` bootstraps the
workspace (first run only), then dispatches
``BackendDispatcherService.start`` → :class:`CLIProcessRuntimeBackend`,
which spawns one detached ``magi runtime`` subprocess via ``subprocess.Popen``
with ``start_new_session=True``.  The launcher exits after spawn; the child
is reparented to ``init`` and continues independently.  One MAGI crashing
does not affect any other.

``magi cli install-service`` registers one systemd user unit per MAGI.
The units ``ExecStart=magi runtime`` directly and bypass the backend today —
Phase 5 unifies that path with the launcher flow.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from magi.launcher.paths import (
    MAGIC_DIR_NAME,
    control_secret_path,
    default_data_root,
    launcher_state_path,
    magis_home,
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
        "run `magi cli start` once first to bootstrap."
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
    magic_dir = data_root / MAGIC_DIR_NAME

    # First run: no slots yet.  The seed created Adam as id=1.
    if not magic_dir.exists() or not any(
        p.is_dir() for p in magic_dir.iterdir()
    ):
        # Default: first MAGI → eva-000
        if not name or name.strip().lower() in ("adam", "eva-00", "eva-000", "1"):
            return 1, "eva-000"
        raise SystemExit(
            f"--name={name!r} requested but no MAGIC slots exist yet. "
            "Run `magi cli start` once first (defaults to first MAGI)."
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
        "Pick one with `magi cli start --name <slug>`."
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


# ──────────────────────────────────────────────────────────────────────────── #
# commands
# ──────────────────────────────────────────────────────────────────────────── #


def _start_webui_subprocess(data_root: Path, port: int) -> str:
    """Spawn ``magi webui`` as a detached subprocess; return its URL.

    Mirrors the runtime subprocess lifecycle — ``start_new_session=True``
    detaches the webui into its own session so the launcher can exit
    without orphaning it. Logs go to ``/tmp/magi-webui.log`` (append) so
    a failed webui (npm / build / port collision) leaves a trail the
    operator can inspect. The launcher does NOT wait on the subprocess;
    a caller who wants liveness should probe ``http://127.0.0.1:{port}/health``.

    Returns the URL string so the caller can print it and (if asked)
    open it in the browser.
    """
    env = os.environ.copy()
    env["HOST_WORKSPACE_DIR"] = str(data_root)
    env["MAGI_PORT"] = str(port)
    from magi.launcher.paths import magis_db_path

    env["MAGIS_DATABASE_URL"] = f"sqlite:///{magis_db_path(data_root, 1, 'genesis')}"
    # No uvicorn autoreload for the auto-spawned webui — it's a smoke
    # test, not a dev-loop tool. Operators who want HMR run
    # ``magi cli webui`` directly.
    env["MAGI_RELOAD"] = "0"
    log_path = Path("/tmp/magi-webui.log")
    log_fh = open(log_path, "ab")
    subprocess.Popen(
        [sys.executable, "-m", "magi", "webui"],
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    return f"http://127.0.0.1:{port}"


def cmd_start(args: argparse.Namespace) -> int:
    """``magi cli start`` — bootstrap once, then start the runtime AND the WebUI.

    First boot seeds Genesis + Adam into the per-MAGIS SQLite.
    Subsequent calls skip the seed and dispatch straight to
    ``bus.runtime.start`` + the WebUI spawn.

    By default BOTH processes come up:

    - the runtime subprocess (``magi runtime``) on its allocated port;
    - the WebUI subprocess (``magi webui``) on :42069 — overridable
      via ``--webui-port``.

    Both spawn detached via ``start_new_session=True``; the launcher
    exits after ``record_spawn`` succeeds and the subprocesses are
    reparented to ``init`` and continue independently. One MAGI
    crashing does not affect any other.

    Skip the WebUI with ``--no-webui`` for CI / scripted flows where
    the operator already has another control-plane view. Skip the
    browser auto-open with ``--no-open`` (the WebUI URL is printed
    either way).

    systemd units installed by :func:`cmd_install_service` take a
    separate path (they ``ExecStart=magi runtime`` directly) and bypass
    the backend — Phase 5 unifies both routes.
    """
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()

    # Propagate the resolved data root into the env so downstream
    # ``magi.launcher.paths`` resolvers (and ``init_orm`` →
    # ``get_engine()``) see the same root the launcher is using.
    # The Composition Root owns this for the lifetime of cmd_start;
    # child runtimes re-derive it from the systemd ``Environment=``
    # block, so no cross-process pollution.
    os.environ["HOST_WORKSPACE_DIR"] = str(data_root)

    # ── control-plane bootstrap (first run only) ──
    home = magis_home(data_root)
    secret_path = control_secret_path(home)
    ensure_control_secret(secret_path)
    state_path = launcher_state_path(home)
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

    # Idempotent seed — creates Genesis + Adam on first run and wires
    # the BUS with control_registry populated.
    _bootstrap_once(data_root)

    # Inject MAGI_BACKEND=cli so the factory picks CLIProcessRuntimeBackend.
    os.environ["MAGI_BACKEND"] = "cli"

    # ── resolve which MAGI to run ──
    magic_id, slug = _resolve_runtime(data_root, getattr(args, "name", None))

    # ── ensure the per-MAGI workspace slot exists ──
    ws_root = data_root / MAGIC_DIR_NAME / slug / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    from magi.launcher.paths import bootstrap_workspace
    bootstrap_workspace(ws_root)

    # ── dispatch start via the backend ──
    from magi.bus import get_bus
    from magi.bus.protocols.lifecycle import RuntimeSpec

    bus = get_bus()
    spec = RuntimeSpec(magic_id=magic_id, name=slug)
    result = bus.runtime.start(spec)
    if result.observed_state != "running":
        print(f"failed to start: {result.message}", file=sys.stderr)
        return 1

    base_url = result.endpoint.base_url if result.endpoint else "(no endpoint)"

    # ── spawn the WebUI alongside the runtime (default on) ──
    webui_url: str | None = None
    if not getattr(args, "no_webui", False):
        webui_port = getattr(args, "webui_port", 42069) or 42069
        try:
            webui_url = _start_webui_subprocess(data_root, port=webui_port)
        except Exception as exc:  # never let a failed webui abort cmd_start
            logger.warning("webui subprocess failed to spawn: %s", exc)

    if not getattr(args, "no_open", False):
        # Prefer the WebUI URL for the browser — that's where the
        # operator actually clicks things; the runtime URL is the
        # underlying API target the WebUI proxies to.
        open_browser(webui_url or base_url)
    print(f"MAGI {magic_id} started — {base_url}")
    if webui_url:
        print(f"WebUI started   — {webui_url}")
    logger.info(
        "MAGI subprocess spawned",
        extra={
            "magic_id": magic_id,
            "slug": slug,
            "backend_ref": result.backend_ref,
            "endpoint": base_url,
            "webui_url": webui_url,
        },
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """``magi cli status`` — list MAGIC slots and their process state."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    magic_dir = data_root / MAGIC_DIR_NAME
    if not magic_dir.exists():
        print("(no MAGIC slots — run `magi cli start` first)")
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
    """``magi cli stop`` — stop every MAGI via the CLI backend.

    Reads runtime state from the control registry and dispatches
    ``bus.runtime.stop`` for each.  The backend sends ``SIGTERM`` and
    falls back to ``SIGKILL`` after a 10 s grace period.

    systemd-managed units should use ``systemctl --user stop
    magi-<slug>.service`` instead.
    """
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()

    from magi.launcher import bootstrap_local
    bootstrap_local(data_root)
    os.environ["MAGI_BACKEND"] = "cli"

    from magi.bus import get_bus
    from magi.bus.protocols.lifecycle import RuntimeSpec

    bus = get_bus()
    runtimes = bus.control_registry.list_runtimes()
    if not runtimes:
        print("(no runtimes registered)")
        return 0

    for r in runtimes:
        spec = RuntimeSpec(magic_id=r.runtime_id)
        try:
            result = bus.runtime.stop(spec)
            print(
                f"stopped runtime {r.runtime_id}: {result.observed_state} ({result.message})"
            )
        except Exception as exc:
            print(
                f"failed to stop runtime {r.runtime_id}: {exc}",
                file=sys.stderr,
            )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """``magi cli doctor`` — surface workspace + control-plane state."""
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    home = magis_home(data_root)
    magic_dir = data_root / MAGIC_DIR_NAME
    slots = sorted(
        p.name for p in magic_dir.iterdir() if p.is_dir()
    ) if magic_dir.exists() else []

    print(json.dumps({
        "data_root": str(data_root),
        "platform": current_platform(),
        "paths": {
            "magis_home": str(home),
            "magis_db": str(home / "magis.db"),
            "magic_slots": slots,
        },
        "magis_db_exists": (home / "magis.db").exists(),
        "secret_exists": control_secret_path(home).exists(),
        "launcher_state_exists": launcher_state_path(home).exists(),
    }, indent=2))
    return 0


# ──────────────────────────────────────────────────────────────────────────── #
# systemd service registration (Linux only)
# ──────────────────────────────────────────────────────────────────────────── #

_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
_SERVICE_TEMPLATE = (
    Path(__file__).resolve().parent.parent.parent
    / "deploy" / "cli" / "service" / "magi@.service"
)


_SERVICE_TEMPLATE_CONTENT = """\
[Unit]
Description=MAGI runtime — {slug} (magic_id={magic_id})
Documentation=https://github.com/realTaki/MAGI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOST_WORKSPACE_DIR={data_root}
Environment=MAGI_RUNTIME_ID={magic_id}
Environment=MAGI_NAME={slug}
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
    magic_dir = data_root / MAGIC_DIR_NAME
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


def cmd_webui(args: argparse.Namespace) -> int:
    """``magi cli webui`` — Vite dev server (HMR) + FastAPI control plane.

    Two ways to run the WebUI on a host:

    - **Dev mode** (``--dev``, default) — spawn the React/Vite dev server
      with HMR on :42069 and replace this process with ``magi webui``
      (FastAPI control plane) on :8000.  Vite proxies ``/api`` → :8000
      so the browser always reaches the UI at ``http://127.0.0.1:42069``.
      Saves the operator from running ``npm run dev`` separately and
      gives them HMR on React edits, just like k8s-dev's
      ``control-dev`` overlay does inside a Pod.
    - **Production mode** (``--no-dev``) — exec directly into
      ``magi webui`` on :42069 with the built SPA mounted (no HMR).
      Use this when you just want to ship the CLI profile without a
      dev loop.

    Each MAGI is still an independent process — this command only
    owns the WebUI / vite process tree; the Adam runtime started via
    ``magi cli start`` runs separately on its own port.
    """
    data_root = Path(args.data_dir) if args.data_dir else default_data_root()

    # Make sure the MAGIS schema + control plane are in place — the
    # webui reads from ``MAGI_Societies/<id>-<slug>/magis.db`` and authenticates
    # against ``control/control-secret``.
    home = magis_home(data_root)
    ensure_control_secret(control_secret_path(home))

    # Reuse the same idempotent seed as ``start`` so a brand-new
    # workspace works without the operator running ``start`` first.
    _bootstrap_once(data_root)

    if getattr(args, "no_dev", False):
        # Production path — same as ``magi webui`` directly on 42069.
        env = os.environ.copy()
        env["HOST_WORKSPACE_DIR"] = str(data_root)
        from magi.launcher.paths import magis_db_path
        env["MAGIS_DATABASE_URL"] = (
            f"sqlite:///{magis_db_path(data_root, 1, 'genesis')}"
        )
        logger.info("launching magi webui on :42069 (no vite, built SPA)")
        os.execvpe(
            sys.executable,
            [sys.executable, "-m", "magi", "webui"],
            env,
        )
        return 0  # unreachable

    # ── Dev path: vite on 42069 + FastAPI control plane on 8000 ──
    webui_dir = Path(__file__).resolve().parents[1] / "WebUI"
    if not webui_dir.is_dir():
        print(
            f"error: WebUI sources not found at {webui_dir}",
            file=sys.stderr,
        )
        return 2
    if not (webui_dir / "node_modules").is_dir():
        print(
            f"[magi] node_modules missing — running `npm ci` in {webui_dir}...",
            file=sys.stderr,
        )
        try:
            subprocess.run(
                ["npm", "ci", "--no-audit", "--no-fund", "--prefer-offline"],
                cwd=str(webui_dir),
                check=True,
            )
        except FileNotFoundError:
            print(
                "error: `npm` not on PATH. Install Node.js ≥ 18 to use "
                "the dev-mode WebUI (`--no-dev` does not require npm).",
                file=sys.stderr,
            )
            return 127

    vite_env = {
        "PATH": os.environ.get("PATH", ""),
        "VITE_BACKEND_URL": "http://127.0.0.1:8000",
        "MAGI_PORT": "8000",  # vite falls back to MAGI_PORT when VITE_BACKEND_URL is unset
    }
    logger.info("spawning vite dev server on :42069 (HMR enabled)")
    vite = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", "42069", "--strictPort"],
        cwd=str(webui_dir),
        env=vite_env,
        stdin=subprocess.DEVNULL,
    )

    # Forward SIGTERM/SIGINT to vite so Ctrl-C in this terminal stops
    # both vite and the eventual FastAPI control plane cleanly.  Once
    # execve replaces this process, signal handlers stop mattering —
    # the FastAPI parent (this very process image) will already be
    # shutting down because vite died.
    def _shutdown(signum, _frame):
        try:
            vite.terminate()
            vite.wait(timeout=5)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Replace this process with ``magi webui`` on :8000 — vite proxies
    # /api → :8000 from the browser's perspective.
    env = os.environ.copy()
    env["HOST_WORKSPACE_DIR"] = str(data_root)
    env["MAGI_PORT"] = "8000"
    from magi.launcher.paths import magis_db_path
    env["MAGIS_DATABASE_URL"] = f"sqlite:///{magis_db_path(data_root, 1, 'genesis')}"
    env["MAGI_RELOAD"] = "1" if not getattr(args, "no_reload", False) else "0"
    logger.info(
        "replacing process with magi webui on :8000 (vite owns :42069)",
        extra={"data_root": str(data_root), "reload": env["MAGI_RELOAD"]},
    )
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "magi", "webui"],
        env,
    )
    return 0  # unreachable


def cmd_install_service(args: argparse.Namespace) -> int:
    """``magi cli install-service`` — register one systemd unit per MAGI."""
    if current_platform() != "linux":
        print(
            f"error: install-service is Linux-only (current platform: {current_platform()}). "
            "On macOS, see deploy/cli/service/magi.plist.example.  "
            "On Windows, see deploy/cli/service/magi-task.xml.",
            file=sys.stderr,
        )
        return 2

    data_root = Path(args.data_dir) if args.data_dir else default_data_root()
    magi_bin = _magi_executable()
    from magi.launcher.paths import magis_db_path
    magis_db = magis_db_path(data_root, 1, "genesis")

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
            "Install systemd or run `magi cli start` manually.",
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
    """``magi cli uninstall-service`` — remove all magi-*.service units."""
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
        prog="magi cli",
        description="MAGI CLI Profile — each MAGI is an independent process.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p = sub.add_parser(
        "start",
        help="bootstrap once, then start the runtime AND the WebUI",
    )
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.add_argument("--name", "-n", default=None, help="MAGI slug or <id>-<slug> to run")
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p.add_argument(
        "--no-webui",
        action="store_true",
        help="don't start the WebUI alongside the runtime (default: start it on :42069)",
    )
    p.add_argument(
        "--webui-port",
        type=int,
        default=42069,
        help="WebUI listen port when started by `start` (default 42069)",
    )
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

    # webui — Vite dev server (HMR) + FastAPI control plane
    p = sub.add_parser(
        "webui",
        help="run Vite dev server (HMR) + FastAPI control plane on :42069",
    )
    p.add_argument("--data-dir", default=None, help="override the data root")
    p.add_argument(
        "--no-dev",
        action="store_true",
        help="run the built SPA directly (no Vite, no HMR)",
    )
    p.add_argument(
        "--no-reload",
        action="store_true",
        help="don't enable uvicorn autoreload on the control plane",
    )
    p.set_defaults(handler=cmd_webui)

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
