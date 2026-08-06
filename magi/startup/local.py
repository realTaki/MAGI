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

import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from magi.startup.config import ConfigurationError, DEFAULT_MAGI_NAME, StartupConfig
from magi.startup.paths import (
    ensure_host_workspace,
    ensure_workspace,
    resolve_runtime_log_paths,
    resolve_runtime_pid_path,
)

logger = logging.getLogger("magi.startup.local")


# Defaults — matches the legacy launcher's behaviour.
DEFAULT_PORT = 42069
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
    port: int = DEFAULT_PORT,
) -> int:
    """Create a new MAGI under an existing MAGIS.

    Per plan §16 — refuses if ``MAGIS_DATABASE_URL`` is unset (the
    caller is expected to bootstrap MAGIS first via :command:`magi run`
    which produces the Genesis ``eva-000``). Persists identity in the
    MAGIS database, ensures the per-MAGI workspace directory, then
    optionally spawns the subprocess.
    """
    if config.is_first_magi:
        raise ConfigurationError(
            "create_magi requires an existing MAGIS — "
            "set MAGIS_DATABASE_URL or run `magi run` first"
        )
    ensure_host_workspace(config.host_workspace_dir)
    ensure_workspace(config.workspace_dir)

    # The legacy launcher re-uses the seeded Genesis identity if
    # --name=eva-000 is supplied. For new names we delegate to the
    # bus service to create the row + membership.
    from sqlalchemy import select

    from magi.bus import get_bus
    from magi.bus.db.models.magis.magic import MAGIC

    bus = get_bus()
    with bus.magis.session_scope() as session:
        existing = session.scalar(
            select(MAGIC).where(MAGIC.name == config.magi_name).limit(1)
        )
        if existing is None:
            logger.info(
                "create_magi: registering %s in MAGIS",
                config.magi_name,
            )
            bus.magic.create_magic(name=config.magi_name)

    if not start:
        return 0

    return start_magi(config=config, port=port)


# ----------------------------------------------------------------------
# start — spawn detached subprocess
# ----------------------------------------------------------------------


def start_magi(
    *,
    config: StartupConfig,
    port: int = DEFAULT_PORT,
) -> int:
    """Spawn one MAGI subprocess; return its PID.

    Refuses to spawn if a live PID file already exists for the same
    workspace.
    """
    pid_path = resolve_runtime_pid_path(config.workspace_dir)
    if pid_path.exists():
        existing = _read_pid(pid_path)
        if existing is not None and _is_alive(existing):
            print(
                f"MAGI {config.magi_name!r} is already running (pid={existing})",
                file=sys.stderr,
            )
            return 1

    ensure_workspace(config.workspace_dir)
    env = _build_subprocess_env(config, port)
    argv = _build_subprocess_argv(port)

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
            "port": port,
            "magi_name": config.magi_name,
        },
    )
    proc = subprocess.Popen(argv, **popen_kwargs)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    if not _wait_healthy(port):
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        print(
            f"MAGI {config.magi_name!r} failed health check on port {port}",
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
    pid = _read_pid(pid_path)
    if pid is None:
        print(f"MAGI {config.magi_name!r}: no PID file", file=sys.stderr)
        return 1
    if not _is_alive(pid):
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
        if not _is_alive(pid):
            break
        time.sleep(STOP_POLL_INTERVAL_S)
    if _is_alive(pid) and not force:
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


def restart_magi(*, config: StartupConfig, port: int = DEFAULT_PORT) -> int:
    """Stop (force) then start. Used by ``magi restart``."""
    stop_magi(config=config, force=True)
    return start_magi(config=config, port=port)


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


def status_magi(*, config: StartupConfig) -> LocalSlotStatus:
    """Return the current status of one MAGI's local slot."""
    pid_path = resolve_runtime_pid_path(config.workspace_dir)
    log_stdout, log_stderr = resolve_runtime_log_paths(config.workspace_dir)
    pid = _read_pid(pid_path)
    alive = bool(pid and _is_alive(pid))
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


def _build_subprocess_env(config: StartupConfig, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOST_WORKSPACE_DIR": str(config.host_workspace_dir),
            "MAGI_NAME": config.magi_name,
            "MAGIS_DATABASE_URL": config.magis_database_url
            or env.get("MAGIS_DATABASE_URL", ""),
            "MAGI_PORT": str(port),
        }
    )
    if config.magi_id:
        env["MAGI_ID"] = str(config.magi_id)
    return env


def _build_subprocess_argv(port: int) -> list[str]:
    """Build the ``magi run`` argv for one detached MAGI subprocess.

    Plan §16 — the child is always ``magi run`` (the unified runtime
    entry point in :mod:`magi.startup.cli`). Host / port are not
    operator-tweakable per plan §5.
    """
    return [sys.executable, "-m", "magi", "run"]


def _read_pid(pid_path: Path) -> int | None:
    if not pid_path.exists():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _wait_healthy(port: int) -> bool:
    import httpx

    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT_S
    url = f"http://127.0.0.1:{port}/health"
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
    "DEFAULT_PORT",
    "LocalSlotStatus",
    "create_magi",
    "start_magi",
    "stop_magi",
    "restart_magi",
    "status_magi",
    "list_slots",
]