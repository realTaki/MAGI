"""Singleton WebUI lifecycle — plan §15.

The whole MAGIS has exactly one WebUI. It is created and recovered
alongside ``eva-000`` only; subsequent MAGIs never start a second
WebUI.

Local responsibilities:

- :func:`start_webui` — spawn detached ``magi webui`` subprocess.
- :func:`stop_webui`  — SIGTERM the subprocess via PID file.
- :func:`ensure_webui_running` — idempotent singleton start.
- :func:`get_webui_status` — current state.

The WebUI product code stays in ``magi.channels.api.app``. This module
only wires the process / PID / log bookkeeping around it.
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
from typing import Optional

from magi.startup.config import DEFAULT_MAGI_NAME, StartupConfig
from magi.startup.paths import (
    resolve_webui_log_paths,
    resolve_webui_pid_path,
)

logger = logging.getLogger("magi.startup.webui")


DEFAULT_WEBUI_PORT = 42069


@dataclass(frozen=True)
class WebUIStatus:
    """Status payload returned by :func:`get_webui_status`."""

    pid: int | None
    alive: bool
    port: int | None
    pid_file: str
    log_stdout: str
    log_stderr: str


# ----------------------------------------------------------------------
# Local lifecycle
# ----------------------------------------------------------------------


def start_webui(
    *,
    config: StartupConfig,
    port: int = DEFAULT_WEBUI_PORT,
) -> str:
    """Spawn the singleton WebUI subprocess; return its URL.

    Per plan §15 — only called when bootstrapping the first MAGI or
    when explicitly recovering the singleton (e.g. after a crash).
    """
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    if pid_path.exists():
        existing = _read_pid(pid_path)
        if existing is not None and _is_alive(existing):
            print(
                f"WebUI already running (pid={existing}); leaving alone",
                file=sys.stderr,
            )
            return f"http://127.0.0.1:{port}"

    env = _build_webui_env(config, port)
    argv = [sys.executable, "-m", "magi", "webui"]

    log_stdout, log_stderr = resolve_webui_log_paths(config.host_workspace_dir)
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

    proc = subprocess.Popen(argv, **popen_kwargs)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    logger.info(
        "WebUI subprocess spawned",
        extra={"pid": proc.pid, "port": port},
    )
    return f"http://127.0.0.1:{port}"


def stop_webui(*, config: StartupConfig, force: bool = False) -> int:
    """SIGTERM the WebUI subprocess via its PID file."""
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    pid = _read_pid(pid_path)
    if pid is None:
        print("WebUI: no PID file", file=sys.stderr)
        return 1
    if not _is_alive(pid):
        pid_path.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return 0
    # Grace window
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            break
        time.sleep(0.2)
    if _is_alive(pid) and not force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    pid_path.unlink(missing_ok=True)
    return 0


def ensure_webui_running(
    *,
    config: StartupConfig,
    port: int = DEFAULT_WEBUI_PORT,
) -> Optional[str]:
    """Start the WebUI if its PID file is missing or stale.

    Called from the first-MAGI bootstrap. Returns the URL on success,
    ``None`` if the WebUI was already healthy.
    """
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    pid = _read_pid(pid_path)
    if pid and _is_alive(pid):
        return None
    # Stale PID — clean up and start fresh.
    if pid_path.exists():
        pid_path.unlink(missing_ok=True)
    return start_webui(config=config, port=port)


def get_webui_status(*, config: StartupConfig) -> WebUIStatus:
    """Inspect the singleton WebUI process."""
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    log_stdout, log_stderr = resolve_webui_log_paths(config.host_workspace_dir)
    pid = _read_pid(pid_path)
    alive = bool(pid and _is_alive(pid))
    return WebUIStatus(
        pid=pid,
        alive=alive,
        port=None,
        pid_file=str(pid_path),
        log_stdout=str(log_stdout),
        log_stderr=str(log_stderr),
    )


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _build_webui_env(config: StartupConfig, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOST_WORKSPACE_DIR": str(config.host_workspace_dir),
            "MAGI_PORT": str(port),
            # No uvicorn autoreload for the auto-spawned webui — it's a
            # smoke test, not a dev-loop tool.
            "MAGI_RELOAD": "0",
        }
    )
    if config.magis_database_url:
        env["MAGIS_DATABASE_URL"] = config.magis_database_url
    return env


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


# ----------------------------------------------------------------------
# Kubernetes side — to be implemented in :mod:`magi.startup.kubernetes`
# ----------------------------------------------------------------------


def ensure_webui_deployment(*, config: StartupConfig) -> None:
    """K8s side of the singleton WebUI.

    The Kubernetes implementation lives in
    :mod:`magi.startup.kubernetes`; this function is the contract
    that :func:`bootstrap_magi` calls after first-MAGI bootstrap.
    """
    try:
        from magi.startup.kubernetes import (
            ensure_webui_deployment as _ensure,
        )
    except ImportError as exc:  # pragma: no cover — k8s module is optional
        logger.debug("Kubernetes deployment skipped: %s", exc)
        return
    _ensure(config=config)


def ensure_webui_service(*, config: StartupConfig) -> None:
    """K8s side of the singleton WebUI Service (external)."""
    try:
        from magi.startup.kubernetes import (
            ensure_webui_service as _ensure,
        )
    except ImportError as exc:  # pragma: no cover
        logger.debug("Kubernetes service skipped: %s", exc)
        return
    _ensure(config=config)


def delete_webui_resources(*, config: StartupConfig) -> None:
    """K8s side — delete the WebUI Deployment + Service (singleton only)."""
    try:
        from magi.startup.kubernetes import (
            delete_webui_resources as _delete,
        )
    except ImportError as exc:  # pragma: no cover
        logger.debug("Kubernetes delete skipped: %s", exc)
        return
    _delete(config=config)


__all__ = [
    "DEFAULT_WEBUI_PORT",
    "WebUIStatus",
    "start_webui",
    "stop_webui",
    "ensure_webui_running",
    "get_webui_status",
    "ensure_webui_deployment",
    "ensure_webui_service",
    "delete_webui_resources",
]