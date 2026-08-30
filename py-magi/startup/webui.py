"""Singleton WebUI lifecycle — plan §15.

The whole MAGIS has exactly one WebUI. It is created and recovered
alongside ``eva-000`` only; subsequent MAGIs never start a second
WebUI.

Local responsibilities:

- :func:`start_webui` — spawn detached ``magi webui`` subprocess.
- :func:`stop_webui`  — SIGTERM the subprocess via PID file.
- :func:`ensure_webui_running` — idempotent singleton start.
- :func:`get_webui_status` — current state.

The WebUI product is now the sibling ``app/`` project. This legacy lifecycle
module remains only until that App service owns web deployment startup.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from startup.config import WEBUI_PORT, StartupConfig
from startup.paths import (
    resolve_magis_database_url,
    resolve_webui_log_paths,
    resolve_webui_pid_path,
)
from startup.process import find_listener_on_port, is_alive, read_pid, reap_orphan_listener

logger = logging.getLogger("startup.webui")


# Re-export so legacy callers importing ``from startup.webui import
# DEFAULT_WEBUI_PORT`` keep working while the canonical constant lives in
# :mod:`startup.config`.  Plan §21 — port is hardcoded.
DEFAULT_WEBUI_PORT: int = WEBUI_PORT

# WebUI bind host — overridable per-deploy via the ``MAGI_WEBUI_HOST``
# environment variable.  CLI / single-machine installs default to
# loopback (only the operator's own browser should reach it); Kubernetes
# deployments set ``MAGI_WEBUI_HOST=0.0.0.0`` so the ClusterIP / NodePort
# can forward traffic into the pod.
DEFAULT_WEBUI_HOST: str = "127.0.0.1"


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
    host: str | None = None,
) -> str:
    """Spawn the singleton WebUI subprocess; return its URL.

    Per plan §15 — only called when bootstrapping the first MAGI or
    when explicitly recovering the singleton (e.g. after a crash).
    ``port`` is hardcoded by :data:`WEBUI_PORT`; the parameter is
    retained for tests / future tunability but the CLI does not expose
    it (plan §21).  ``host`` is resolved from ``MAGI_WEBUI_HOST`` (or
    :data:`DEFAULT_WEBUI_HOST`); only used for display URL / logging,
    the actual bind happens in :func:`run_webui_foreground`.
    """
    host = host or os.environ.get("MAGI_WEBUI_HOST", DEFAULT_WEBUI_HOST)
    pid_path = resolve_webui_pid_path(config.host_workspace_dir)
    if pid_path.exists():
        existing = read_pid(pid_path)
        if existing is not None and is_alive(existing):
            print(
                f"WebUI already running (pid={existing}); leaving alone",
                file=sys.stderr,
            )
            return f"http://127.0.0.1:{port}"
        # Stale PID file — drop it before we try to claim the slot.
        pid_path.unlink(missing_ok=True)

    reap_orphan_listener(port, label="WebUI orphan worker")

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
) -> str | None:
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
    """Reject the removed legacy WebUI command.

    ``MAGI_WEBUI_PORT`` is set by the detached launcher when an internal
    caller needs a non-default port. Production keeps the canonical
    :data:`WEBUI_PORT` default.

    Foreground-mode lifecycle (mirrors :func:`start_webui`):

    1. Claim ``<host_workspace>/run/webui.pid`` with ``os.getpid()``
       so ``magi webui stop`` can find this process.  Refuse to start
       if the file points at a still-alive PID.
    2. Install SIGTERM/SIGINT handlers that unlink the PID file.  The
       handler runs once even if uvicorn re-raises the signal.

    Unlike :func:`startup.runtime.run_magi`, no extra cleanup is
    wired: the WebUI does not own a ``runtime_state`` row, so SIGTERM
    only needs to drop the PID file.
    """
    del config
    raise RuntimeError(
        "The legacy py-magi WebUI has been removed; run the sibling MAGI App service instead."
    )


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _reap_orphan_worker(port: int) -> None:
    """Kill any orphan worker still listening on the WebUI port.

    Mirror of :func:`startup.local._reap_orphan_worker` for the
    WebUI process recovery: a crash can leave a child listening with no
    PID-file owner, which would otherwise block the next spawn with
    ``Address already in use``.
    """
    orphan = find_listener_on_port(port)
    if orphan is None:
        return
    print(
        f"WebUI orphan worker pid={orphan} holds port {port}; killing before respawn",
        file=sys.stderr,
    )
    try:
        os.kill(orphan, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if find_listener_on_port(port) is None:
            return
        time.sleep(0.05)


def _build_webui_env(config: StartupConfig, port: int) -> dict[str, str]:
    """Build the env passed to the detached ``magi webui`` subprocess.

    Plan §15 — the WebUI owns the operator-facing port. Runtime reload is not
    configurable, so no reload knob escapes this subprocess.
    """
    env = os.environ.copy()
    env["HOST_WORKSPACE_DIR"] = str(config.host_workspace_dir)
    # Pass the resolved WebUI port explicitly so the child does not have
    # to re-read MAGIC_DEFAULTS.  Plan §21 forbids operator configurability
    # — this is internal-only communication.  ``MAGI_WEBUI_HOST`` is
    # propagated via ``os.environ.copy()`` (k8s yaml sets it explicitly;
    # CLI defaults to loopback in the child).
    env["MAGI_WEBUI_PORT"] = str(port)
    env["MAGIS_DATABASE_URL"] = config.magis_database_url or resolve_magis_database_url(
        config.host_workspace_dir, config.magis_name
    )
    env["MAGIS_NAME"] = config.magis_name
    # No ``MAGI_CONTROL_SECRET`` is propagated: the WebUI reads the
    # HMAC key directly from the per-MAGIS ``control_secrets`` row at
    # request time.  The detached child reopens the same MAGIS store
    # via ``MAGIS_DATABASE_URL`` and resolves the secret itself.
    return env


def _read_control_secret(*, magis_url: str, magis_name: str) -> str | None:
    """Read the raw control secret from the MAGIS database.

    Opens a transient MAGIS facade purely to read the ``control_secrets`` row.
    """
    try:
        from old_bus import open_bus

        bus = open_bus(magis_url=magis_url)
    except Exception:
        return None
    if bus.control_secrets_book is not None:
        row = bus.control_secrets_book.get_by_name(name=magis_name)
        if row is not None and row.secret_value:
            return row.secret_value.decode("utf-8")
    return None


# ----------------------------------------------------------------------
# Kubernetes side — to be implemented in :mod:`startup.kubernetes`
# ----------------------------------------------------------------------


def ensure_webui_deployment(*, config: StartupConfig) -> None:
    """K8s side of the singleton WebUI.

    Builds the manifest from :mod:`startup.kubernetes` and applies
    it via the legacy K8s client.  No-op when the K8s module is
    unavailable.
    """
    try:
        from startup.kubernetes import (
            ensure_webui_deployment as _build,
        )
    except ImportError:
        logger.debug("Kubernetes deployment skipped — no k8s module")
        return
    manifest = _build(config=config)
    logger.info(
        "WebUI Deployment manifest ready: %s",
        manifest.get("deployment", {}).get("metadata", {}).get("name", "?"),
    )


def ensure_webui_service(*, config: StartupConfig) -> None:
    """K8s side of the singleton WebUI Service (external)."""
    try:
        from startup.kubernetes import (
            ensure_webui_service as _build,
        )
    except ImportError:
        logger.debug("Kubernetes service skipped — no k8s module")
        return
    manifest = _build(config=config)
    logger.info(
        "WebUI Service manifest ready: %s",
        manifest.get("service", {}).get("metadata", {}).get("name", "?"),
    )


def delete_webui_resources(*, config: StartupConfig) -> None:
    """K8s side — delete the WebUI Deployment + Service (singleton only)."""
    try:
        from startup.kubernetes import (
            delete_webui_resources as _delete,
        )
    except ImportError:
        logger.debug("Kubernetes delete skipped — no k8s module")
        return
    _delete(config=config)


__all__ = [
    "DEFAULT_WEBUI_PORT",
    "DEFAULT_WEBUI_HOST",
    "WebUIStatus",
    "start_webui",
    "stop_webui",
    "ensure_webui_running",
    "get_webui_status",
    "run_webui_foreground",
    "ensure_webui_deployment",
    "ensure_webui_service",
    "delete_webui_resources",
]
