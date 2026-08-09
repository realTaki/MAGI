"""Local process management — plan §16.

Each MAGI is one OS process. This module owns:

- The on-disk PID / log layout for one MAGI subprocess.
- :func:`create_magi`, :func:`start_magi`, :func:`stop_magi`,
  :func:`restart_magi`, :func:`status_magi` — the CLI verbs.
- Per-MAGI detached subprocess spawning via
  ``subprocess.Popen(start_new_session=True)``.
- The "first MAGI also starts the WebUI" hook.

It does **not** build Kubernetes resources (see
:mod:`magi.startup.kubernetes`). It does **not** own the WebUI
implementation — only its lifecycle (see :mod:`magi.startup.webui`).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from magi.startup.config import (
    ConfigurationError,
    RUNTIME_PORT,
    StartupConfig,
)
from magi.startup.paths import (
    resolve_runtime_log_paths,
    resolve_runtime_pid_path,
)
from magi.startup.process import is_alive, read_pid

logger = logging.getLogger("magi.startup.local")


# Plan §21 — the Runtime's internal port is hardcoded; this helper
# supervises one Runtime subprocess and probes its health on the same
# loopback port the Runtime binds to.  No operator knob.  Must match
# :data:`magi.startup.config.RUNTIME_PORT`.
HEALTH_PROBE_PORT = RUNTIME_PORT
HEALTH_POLL_TIMEOUT_S = 30.0
HEALTH_POLL_INTERVAL_S = 0.5
STOP_GRACE_S = 10.0
STOP_POLL_INTERVAL_S = 0.2


# ----------------------------------------------------------------------
# Data shapes
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class LocalSlotStatus:
    """Per-MAGI local status — the :func:`status_magi` row."""

    magi_name: str
    pid: int | None
    alive: bool
    pid_file: str
    log_stdout: str
    log_stderr: str


# ----------------------------------------------------------------------
# create — register a new MAGI under an existing MAGIS
# ----------------------------------------------------------------------


def create_magi(
    *,
    config: StartupConfig,
    start: bool = True,
) -> int:
    """Create a new MAGI under an existing MAGIS.

    Per plan §16 — refuses if ``MAGIS_DATABASE_URL`` is unset (the
    caller is expected to bootstrap MAGIS first via :command:`magi run`
    which produces the Genesis ``eva-000``). Persists identity in the
    MAGIS database, creates Membership, ensures the per-MAGI workspace
    directory, then optionally spawns the subprocess.
    """
    if config.magis_database_url is None:
        raise ConfigurationError(
            "create_magi requires an existing MAGIS — "
            "set MAGIS_DATABASE_URL or run `magi run` first"
        )
    config.workspace_dir.mkdir(parents=True, exist_ok=True)

    from magi.bus.library.magis import MagisBook, MagisMembershipBook, MagisRoleBook
    from magi.startup.bootstrap import _magis_factory

    factory = _magis_factory(config.magis_database_url)
    magis = MagisBook(factory)
    roles = MagisRoleBook(factory)
    memberships = MagisMembershipBook(factory)
    genesis = magis.get_root()
    if genesis is None:
        raise ConfigurationError("No MAGIS found — bootstrap the first MAGI first")
    eva_role = roles.find(magis_id=genesis.id, name="EVA")
    if eva_role is None:
        eva_role = roles.add(magis_id=genesis.id, name="EVA", is_reserved=True)

    # A MAGI's display name is local Bus setting state, not a global
    # ``magic`` row.  A supplied MAGI_ID is an idempotent re-use request;
    # otherwise registering creates one new membership identity.
    membership = (
        memberships.get(magi_id=int(config.magi_id))
        if config.magi_id and config.magi_id.isdigit()
        else None
    )
    if membership is None:
        membership = memberships.add(magis_id=genesis.id, role_id=eva_role.id)
        logger.info(
            "create_magi: registered %s as membership %s in MAGIS %s",
            config.magi_name, membership.id, genesis.name,
        )

    if not start:
        return 0

    return start_magi(config=replace(config, magi_id=str(membership.id)))


# ----------------------------------------------------------------------
# start — spawn detached subprocess
# ----------------------------------------------------------------------


def start_magi(
    *,
    config: StartupConfig,
) -> int:
    """Spawn one MAGI subprocess; return its PID.

    Refuses to spawn if a live PID file already exists for the same
    workspace.  Per plan §21 the Runtime's port is hardcoded; the
    parent probes the child on the same loopback port.
    """
    pid_path = resolve_runtime_pid_path(config.workspace_dir)
    if pid_path.exists():
        existing = read_pid(pid_path)
        if existing is not None and is_alive(existing):
            print(
                f"MAGI {config.magi_name!r} is already running (pid={existing})",
                file=sys.stderr,
            )
            return 1

    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    env = _build_subprocess_env(config)
    argv = _build_subprocess_argv(config)

    log_stdout, log_stderr = resolve_runtime_log_paths(config.workspace_dir)
    log_stdout.parent.mkdir(parents=True, exist_ok=True)
    log_stderr.parent.mkdir(parents=True, exist_ok=True)

    stdout_fh = open(log_stdout, "ab")
    stderr_fh = open(log_stderr, "ab")

    popen_kwargs: dict = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_fh,
        "stderr": stderr_fh,
        "close_fds": True,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    logger.info(
        "spawning MAGI subprocess",
        extra={
            "argv": argv,
            "host_workspace_dir": str(config.host_workspace_dir),
            "workspace_dir": str(config.workspace_dir),
            "magi_name": config.magi_name,
        },
    )
    proc = subprocess.Popen(argv, **popen_kwargs)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    if not _wait_healthy():
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        print(
            f"MAGI {config.magi_name!r} failed health check on port {HEALTH_PROBE_PORT}",
            file=sys.stderr,
        )
        return 1

    print(f"MAGI {config.magi_name!r} started (pid={proc.pid})")
    return 0


# ----------------------------------------------------------------------
# stop
# ----------------------------------------------------------------------


def stop_magi(*, config: StartupConfig, force: bool = False) -> int:
    """Send SIGTERM (or SIGKILL with ``force=True``) to the subprocess."""
    pid_path = resolve_runtime_pid_path(config.workspace_dir)
    pid = read_pid(pid_path)
    if pid is None:
        print(f"MAGI {config.magi_name!r}: no PID file", file=sys.stderr)
        return 1
    if not is_alive(pid):
        print(f"MAGI {config.magi_name!r}: already dead (pid={pid})")
        pid_path.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return 0

    deadline = time.monotonic() + STOP_GRACE_S
    while time.monotonic() < deadline:
        if not is_alive(pid):
            break
        time.sleep(STOP_POLL_INTERVAL_S)
    if is_alive(pid) and not force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    pid_path.unlink(missing_ok=True)
    print(f"MAGI {config.magi_name!r} stopped (pid={pid})")
    return 0


# ----------------------------------------------------------------------
# restart
# ----------------------------------------------------------------------


def restart_magi(*, config: StartupConfig) -> int:
    """Stop (force) then start. Used by ``magi restart``."""
    stop_magi(config=config, force=True)
    return start_magi(config=config)


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


def status_magi(*, config: StartupConfig) -> LocalSlotStatus:
    """Return the current status of one MAGI's local slot."""
    pid_path = resolve_runtime_pid_path(config.workspace_dir)
    log_stdout, log_stderr = resolve_runtime_log_paths(config.workspace_dir)
    pid = read_pid(pid_path)
    alive = bool(pid and is_alive(pid))
    return LocalSlotStatus(
        magi_name=config.magi_name,
        pid=pid,
        alive=alive,
        pid_file=str(pid_path),
        log_stdout=str(log_stdout),
        log_stderr=str(log_stderr),
    )


def list_slots(host_workspace_dir: Path) -> list[str]:
    """Enumerate MAGI slots under ``<host>/MAGI_Citizens/``."""
    from magi.startup.config import MAGI_CITIZENS_DIR

    host = Path(host_workspace_dir).expanduser().resolve()
    citizens = host / MAGI_CITIZENS_DIR
    if not citizens.is_dir():
        return []
    return sorted(p.name for p in citizens.iterdir() if p.is_dir())


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _build_subprocess_env(config: StartupConfig) -> dict[str, str]:
    """Build the env passed to the detached ``magi run`` subprocess.

    Plan §21 — only the four startup-contract inputs are propagated;
    no ``MAGI_PORT`` / ``MAGI_RELOAD`` knobs leak into the child.
    """
    env = os.environ.copy()
    env["HOST_WORKSPACE_DIR"] = str(config.host_workspace_dir)
    env["MAGI_NAME"] = config.magi_name
    if config.magis_database_url is not None:
        env["MAGIS_DATABASE_URL"] = config.magis_database_url
    if config.magi_id:
        env["MAGI_ID"] = str(config.magi_id)
    return env


def _build_subprocess_argv(config: StartupConfig) -> list[str]:
    """Build the ``magi run`` argv for one detached MAGI subprocess.

    Plan §16 — the child is always ``magi run`` with explicit identity
    args so it works even when env inheritance is disrupted.
    """
    argv = [sys.executable, "-m", "magi", "run", "--name", config.magi_name]
    if config.magis_database_url:
        argv.extend(["--magis", config.magis_database_url])
    if config.magi_id:
        argv.extend(["--magi-id", str(config.magi_id)])
    return argv


def _wait_healthy() -> bool:
    """Poll the child Runtime's ``/health`` endpoint on the loopback port.

    Plan \u00a721 \u2014 the Runtime's port is fixed, so the parent probes the
    same hardcoded :data:`HEALTH_PROBE_PORT`.  No operator override.
    """
    import httpx

    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT_S
    url = f"http://127.0.0.1:{HEALTH_PROBE_PORT}/health"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=1.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(HEALTH_POLL_INTERVAL_S)
    return False


__all__ = [
    "HEALTH_PROBE_PORT",
    "LocalSlotStatus",
    "create_magi",
    "start_magi",
    "stop_magi",
    "restart_magi",
    "status_magi",
    "list_slots",
    # platform (merged from :mod:`magi.startup.platform`)
    "PlatformName",
    "current_platform",
    "open_browser",
    "supports_posix_pgid",
]


# ----------------------------------------------------------------------
# OS detection helpers (was :mod:`magi.startup.platform`)
# ----------------------------------------------------------------------

# Tiny, dependency-free. Phase 6's ``magi cli start`` uses these to
# decide whether the launcher can ``open`` a browser tab, where to
# write the PID file, and how to interpret the supervisor's exit codes.

PlatformName = Literal["macos", "linux", "windows", "other"]


def current_platform() -> PlatformName:
    """Return the platform family as a stable string."""
    name = sys.platform
    if name == "darwin":
        return "macos"
    if name.startswith("linux"):
        return "linux"
    if name == "win32":
        return "windows"
    return "other"


def open_browser(url: str) -> None:
    """Best-effort open ``url`` in the OS default browser.

    Failures are swallowed; the launcher never crashes on missing
    browser support.
    """
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:
        pass


def supports_posix_pgid() -> bool:
    """``pgid`` / ``os.setpgrp`` are POSIX-only; Windows launches without one."""
    return os.name == "posix"
