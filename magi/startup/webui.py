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

from magi.startup.config import StartupConfig, WEBUI_HOST, WEBUI_PORT
from magi.startup.paths import (
    resolve_webui_log_paths,
    resolve_webui_pid_path,
)
from magi.startup.process import is_alive, read_pid

logger = logging.getLogger("magi.startup.webui")


# Re-export so legacy callers importing ``from magi.startup.webui import
# DEFAULT_WEBUI_PORT`` keep working while the canonical constant lives in
# :mod:`magi.startup.config`.  Plan §21 — port is hardcoded.
DEFAULT_WEBUI_PORT: int = WEBUI_PORT


@dataclass(frozen=True)
class WebUIStatus:
    """Status payload returned by :func:`get_webui_status`."""

    pid: int | None
    alive: bool
    port: int | None
    pid_file: str
    log_stdout: str
    log_stderr: str


@dataclass(frozen=True, slots=True)
class ControlContext:
    """Read/open-only control capability for the singleton WebUI process."""

    bus: object


# ----------------------------------------------------------------------
# Local lifecycle
# ----------------------------------------------------------------------


def start_webui(
    *,
    config: StartupConfig,
    port: int = DEFAULT_WEBUI_PORT,
    host: str = WEBUI_HOST,
) -> str:
    """Spawn the singleton WebUI subprocess; return its URL.

    Per plan §15 — only called when bootstrapping the first MAGI or
    when explicitly recovering the singleton (e.g. after a crash).
    ``port`` is hardcoded by :data:`WEBUI_PORT`; the parameter is
    retained for tests / future tunability but the CLI does not expose
    it (plan §21).
    """
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    if pid_path.exists():
        existing = read_pid(pid_path)
        if existing is not None and is_alive(existing):
            print(
                f"WebUI already running (pid={existing}); leaving alone",
                file=sys.stderr,
            )
            return f"http://127.0.0.1:{port}"

    env = _build_webui_env(config, port)
    argv = [sys.executable, "-m", "magi", "webui", "run", "--foreground"]

    log_stdout, log_stderr = resolve_webui_log_paths(config.host_workspace_dir)
    if not log_stdout.parent.is_dir() or not log_stderr.parent.is_dir():
        raise RuntimeError("WebUI logs are not provisioned; run `magi init` first")
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
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    logger.info(
        "WebUI subprocess spawned",
        extra={"pid": proc.pid, "host": host, "port": port},
    )
    return f"http://{host}:{port}".replace("0.0.0.0", "127.0.0.1")


def stop_webui(*, config: StartupConfig, force: bool = False) -> int:
    """SIGTERM the WebUI subprocess via its PID file."""
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    pid = read_pid(pid_path)
    if pid is None:
        print("WebUI: no PID file", file=sys.stderr)
        return 1
    if not is_alive(pid):
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
        if not is_alive(pid):
            break
        time.sleep(0.2)
    if is_alive(pid) and not force:
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
    pid = read_pid(pid_path)
    if pid and is_alive(pid):
        return None
    # Stale PID — clean up and start fresh.
    if pid_path.exists():
        pid_path.unlink(missing_ok=True)
    return start_webui(config=config, port=port)


def get_webui_status(*, config: StartupConfig) -> WebUIStatus:
    """Inspect the singleton WebUI process."""
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    log_stdout, log_stderr = resolve_webui_log_paths(config.host_workspace_dir)
    pid = read_pid(pid_path)
    alive = bool(pid and is_alive(pid))
    return WebUIStatus(
        pid=pid,
        alive=alive,
        port=None,
        pid_file=str(pid_path),
        log_stdout=str(log_stdout),
        log_stderr=str(log_stderr),
    )


def run_webui_foreground(*, config: StartupConfig) -> None:
    """Run the control service without creating node storage or workers."""
    import uvicorn

    from magi.bus import open_control_bus
    from magi.channels.api.app import create_control_app
    from magi.startup.spec import load_runtime_spec

    root_workspace = config.host_workspace_dir / "MAGI_Citizens" / "eva-000"
    spec = load_runtime_spec(root_workspace)
    # This opens only the provisioned control/MAGIS store.  It never opens a
    # node-private ``MAGI_Citizens/<name>/memories/magi.db`` and starts no
    # node worker; target-specific operations are proxied to runtimes.
    bus = open_control_bus(
        control_dir=str(config.host_workspace_dir / "MAGI_Societies" / "genesis" / "control"),
        magis_url=spec.magis_database_url,
    )
    app = create_control_app(context=ControlContext(bus=bus))
    uvicorn.run(app, host=WEBUI_HOST, port=WEBUI_PORT, log_level="info")


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _build_webui_env(config: StartupConfig, port: int) -> dict[str, str]:
    """Build the env passed to the detached ``magi webui`` subprocess.

    Plan §15 — the WebUI owns the operator-facing port.  Reload is
    hardcoded off in production (plan §21): no ``MAGI_RELOAD`` knob
    escapes this subprocess.
    """
    env = os.environ.copy()
    env["HOST_WORKSPACE_DIR"] = str(config.host_workspace_dir)
    # Pass the resolved WebUI port + host explicitly so the child does
    # not have to re-read MAGIC_DEFAULTS.  Plan §21 forbids operator
    # configurability — these are internal-only communication.
    env["MAGI_WEBUI_PORT"] = str(port)
    env["MAGI_WEBUI_HOST"] = WEBUI_HOST
    if config.magis_database_url:
        env["MAGIS_DATABASE_URL"] = config.magis_database_url
    return env


# ----------------------------------------------------------------------
# Kubernetes side — to be implemented in :mod:`magi.startup.kubernetes`
# ----------------------------------------------------------------------


def ensure_webui_deployment(*, config: StartupConfig) -> None:
    """K8s side of the singleton WebUI.

    Builds the manifest from :mod:`magi.startup.kubernetes` and applies
    it via the legacy K8s client.  No-op when the K8s module is
    unavailable.
    """
    try:
        from magi.startup.kubernetes import (
            ensure_webui_deployment as _build,
        )
    except ImportError:
        logger.debug("Kubernetes deployment skipped — no k8s module")
        return
    manifest = _build(config=config)
    logger.info("WebUI Deployment manifest ready: %s", manifest.get("deployment", {}).get("metadata", {}).get("name", "?"))


def ensure_webui_service(*, config: StartupConfig) -> None:
    """K8s side of the singleton WebUI Service (external)."""
    try:
        from magi.startup.kubernetes import (
            ensure_webui_service as _build,
        )
    except ImportError:
        logger.debug("Kubernetes service skipped — no k8s module")
        return
    manifest = _build(config=config)
    logger.info("WebUI Service manifest ready: %s", manifest.get("service", {}).get("metadata", {}).get("name", "?"))


def delete_webui_resources(*, config: StartupConfig) -> None:
    """K8s side — delete the WebUI Deployment + Service (singleton only)."""
    try:
        from magi.startup.kubernetes import (
            delete_webui_resources as _delete,
        )
    except ImportError:
        logger.debug("Kubernetes delete skipped — no k8s module")
        return
    _delete(config=config)


__all__ = [
    "DEFAULT_WEBUI_PORT",
    "WebUIStatus",
    "ControlContext",
    "start_webui",
    "stop_webui",
    "ensure_webui_running",
    "get_webui_status",
    "run_webui_foreground",
    "ensure_webui_deployment",
    "ensure_webui_service",
    "delete_webui_resources",
]
