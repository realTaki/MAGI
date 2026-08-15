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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from magi.startup.config import ConfigurationError, StartupConfig
from magi.startup.paths import (
    resolve_magis_control_dir,
    resolve_runtime_log_paths,
    resolve_runtime_pid_path,
)
from magi.startup.process import find_listener_on_port, is_alive, read_pid
from magi.startup.spec import load_runtime_spec

logger = logging.getLogger("magi.startup.local")


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

    Recovers from the uvicorn reload-storm failure mode: a crashed
    supervisor leaves its worker listening on the runtime port with
    no PID-file owner.  Before spawning we sweep the port for such an
    orphan and SIGKILL it, so a developer can re-run ``magi start``
    after editing source files without manually killing stale workers.
    """
    spec = load_runtime_spec(config.workspace_dir)
    pid_path = resolve_runtime_pid_path(config.workspace_dir)
    if pid_path.exists():
        existing = read_pid(pid_path)
        if existing is not None and is_alive(existing):
            print(
                f"MAGI {config.magi_name!r} is already running (pid={existing})",
                file=sys.stderr,
            )
            return 1
        # Stale PID file — drop it so the fresh spawn can claim the slot.
        pid_path.unlink(missing_ok=True)

    _reap_orphan_worker(spec.runtime_port)

    env = _build_subprocess_env(config)
    argv = _build_subprocess_argv(config)

    log_stdout, log_stderr = resolve_runtime_log_paths(config.workspace_dir)
    if not log_stdout.parent.is_dir() or not log_stderr.parent.is_dir():
        raise ConfigurationError(
            "node logs are not provisioned; run `magi init` or `magi node create`"
        )

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
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    if not _wait_healthy(spec.runtime_port):
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        print(
            f"MAGI {config.magi_name!r} failed health check on port {spec.runtime_port}",
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
        _mark_registry_stopped(config)
        return 1
    if not is_alive(pid):
        print(f"MAGI {config.magi_name!r}: already dead (pid={pid})")
        pid_path.unlink(missing_ok=True)
        _mark_registry_stopped(config)
        return 0
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        _mark_registry_stopped(config)
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
    _mark_registry_stopped(config)
    print(f"MAGI {config.magi_name!r} stopped (pid={pid})")
    return 0


# ----------------------------------------------------------------------
# restart
# ----------------------------------------------------------------------


def restart_magi(*, config: StartupConfig) -> int:
    """Stop (force) then start. Used by ``magi node restart``."""
    stop_magi(config=config, force=True)
    return start_magi(config=config)


# ----------------------------------------------------------------------
# registry reconciliation
# ----------------------------------------------------------------------


def _mark_registry_stopped(config: StartupConfig) -> None:
    """Flip ``runtime_state`` to STOPPED for this MAGI.

    Called from :func:`stop_magi` after the process exits so the
    singleton WebUI's ``/api/auth/available-magi`` endpoint stops
    offering this runtime as a login target.  Best-effort: if the
    MAGIS store isn't reachable or the runtime row is missing, the
    ``start_magi`` path will still reconcile via
    :meth:`set_desired_state` / :meth:`set_observed_state` on the
    next launch.
    """
    try:
        spec = load_runtime_spec(config.workspace_dir)
    except Exception:
        return
    magi_id_raw = spec.magi_id
    try:
        magi_id = int(magi_id_raw)
    except (TypeError, ValueError):
        return

    try:
        from magi.bus.bootstrap import open_control_bus
        from magi.bus.db.base import utcnow_naive
        from magi.bus.library.magis.runtimeBook import (
            RuntimeDesiredState,
            RuntimeObservedState,
        )

        control_dir = str(resolve_magis_control_dir(config.host_workspace_dir, spec.magis_name))
        magis_url = spec.magis_database_url
        bus = open_control_bus(control_dir=control_dir, magis_url=magis_url)
    except Exception:
        return

    runtimes = bus.runtime_state_book
    if runtimes is None:
        return
    try:
        runtimes.set_desired_state(runtime_id=magi_id, desired_state=RuntimeDesiredState.STOPPED)
        runtimes.set_observed_state(
            runtime_id=magi_id,
            observed_state=RuntimeObservedState.STOPPED,
            stopped_at=utcnow_naive(),
        )
    except Exception:
        # Reconciliation is best-effort; do not mask stop_magi's
        # exit status on a registry write failure.
        pass


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


def _reap_orphan_worker(port: int) -> None:
    """Kill any process still listening on ``port`` that has no PID-file owner.

    A uvicorn supervisor crash (most commonly: a reload-storm after
    editing source files while the dev runtime is running) leaves its
    child worker holding the runtime socket with no PID file pointing
    at it.  Without this sweep the next :func:`start_magi` would bind-fail
    and silently strand the slot.  SIGKILL is intentional — the orphan
    is already in a bad state and we want the port released *now*, not
    after a graceful drain that the worker may never attempt.
    """
    orphan = find_listener_on_port(port)
    if orphan is None:
        return
    print(
        f"orphan worker pid={orphan} holds port {port}; killing before respawn",
        file=sys.stderr,
    )
    try:
        os.kill(orphan, signal.SIGKILL)
    except ProcessLookupError:
        pass
    # Wait up to 2 s for the kernel to actually release the socket;
    # SIGKILL is synchronous but the FD teardown can lag the signal.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if find_listener_on_port(port) is None:
            return
        time.sleep(0.05)


def _build_subprocess_env(config: StartupConfig) -> dict[str, str]:
    """Build the env passed to the detached ``magi node run`` subprocess.

    Plan §21 — only the four startup-contract inputs are propagated;
    no ``MAGI_PORT`` / ``MAGI_RELOAD`` knobs leak into the child.
    """
    env = os.environ.copy()
    env["HOST_WORKSPACE_DIR"] = str(config.host_workspace_dir)
    env["MAGI_NAME"] = config.magi_name
    env["MAGIS_NAME"] = config.magis_name
    if config.magis_database_url is not None:
        env["MAGIS_DATABASE_URL"] = config.magis_database_url
    if config.magi_id:
        env["MAGI_ID"] = str(config.magi_id)
    # The runtime verifies webui-originated requests with
    # HMAC over the request line + selected MAGI; that HMAC
    # needs the same shared secret the webui uses. The
    # single-machine install provisions it under the MAGIS
    # control dir at init time; thread it into the node child
    # so the proxy layer can sign-verify inbound requests.
    control_secret_path = resolve_magis_control_dir(
        config.host_workspace_dir, config.magis_name
    ) / "control-secret"
    if control_secret_path.exists():
        env["MAGI_CONTROL_SECRET"] = control_secret_path.read_text(
            encoding="utf-8"
        ).strip()
    return env


def _build_subprocess_argv(config: StartupConfig) -> list[str]:
    """Build the foreground child command for one provisioned MAGI."""
    return [
        sys.executable,
        "-m",
        "magi",
        "node",
        "run",
        "--foreground",
        "--name",
        config.magi_name,
    ]


def _wait_healthy(port: int) -> bool:
    """Poll the child Runtime's ``/health`` endpoint on the loopback port.

    Plan \u00a721 \u2014 the Runtime's port is fixed, so the parent probes the
    same hardcoded :data:`HEALTH_PROBE_PORT`.  No operator override.
    """
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
    "LocalSlotStatus",
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

# Tiny, dependency-free. The managed node launcher uses these to
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
