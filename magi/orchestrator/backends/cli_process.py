"""CLI Profile ``RuntimeBackend`` — subprocess spawn, one MAGI per process.

Per architecture intent, each CLI-deploy MAGI is an independent OS
process. This backend wraps :class:`subprocess.Popen` with
``start_new_session=True`` so the child is detached from the launcher
and reparented to ``init`` when the launcher exits — one MAGI crashing
does not affect any other.

``bus.runtime.start`` / ``stop`` / ``delete`` is the single lifecycle
entry point, identical to the K8s path.  ``magi cli start <name>``
calls :class:`magi.bus.services.runtime.BackendDispatcherService` →
:func:`magi.orchestrator.backends.factory.create` → :meth:`start`.

The backend tolerates ``control_registry=None`` — the runtime-process
BUS has no local SQLite engine, so ``bus.control_registry`` is ``None``
in that context.  When the launcher / supervisor wired the registry,
this backend records spawn / stop / port allocation through it; when
not wired (runtime-process case), the methods still return the
correct DTOs.

Multi-MAGI supervisor / restart policy / health-check loop /
orchestrator-daemon mode land in Phase 5.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from magi.bus.protocols.lifecycle import (
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)
from magi.bus.protocols.runtime import RuntimeEndpoint
from magi.bus.services.control_registry import ControlRegistryService

DEFAULT_PORT = 42069
HEALTH_POLL_TIMEOUT_S = 30.0
HEALTH_POLL_INTERVAL_S = 0.5
STOP_GRACE_S = 10.0
STOP_POLL_INTERVAL_S = 0.2


class CLIProcessRuntimeBackend:
    kind = "cli"

    def __init__(
        self,
        control_registry: Optional[ControlRegistryService] = None,
    ) -> None:
        if control_registry is None:
            try:
                from magi.bus import get_bus
                control_registry = get_bus().control_registry
            except Exception:
                control_registry = None
        self._control = control_registry

    # ── provision ────────────────────────────────────────────────────── #

    def provision_magis(
        self,
        magis_id: int,
        magis_name: str,
    ) -> MagisProvisionResult:
        """Return the platform-neutral provision result for Local Profile.

        The Local Profile provisions its MAGIS SQLite through
        :func:`magi.launcher.bootstrap_local` (composition-root stage)
        before any backend method is invoked.  This backend therefore
        treats ``provision_magis`` as a no-op — it returns the
        platform-neutral DTO so the BUS / API layer can observe the
        intent, but does not create or migrate storage on its own.
        The K8s backend, by contrast, creates a fresh PostgreSQL on
        every call; the asymmetry is by design (Local is single-host,
        K8s is multi-tenant).
        """
        from magi.launcher.paths import magis_db_path

        magis_db = magis_db_path(_data_root(), magis_id, magis_name)
        return MagisProvisionResult(
            magis_id=magis_id,
            backend_kind="cli",
            database_service_name=None,
            workspace_claim_name=None,
            message=(
                f"CLI Profile — per-MAGIS SQLite pre-created at "
                f"{magis_db} by bootstrap_cli(); this backend is a no-op"
            ),
        )

    # ── start / stop / delete / endpoint_for ─────────────────────────── #

    def start(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        port = self._allocate_port(spec.magic_id)
        backend_ref, pid = self._spawn(spec, port)
        if not self._wait_healthy(port):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return RuntimeOperationResult(
                runtime_id=spec.magic_id,
                backend_kind="cli",
                backend_ref=backend_ref,
                observed_state="failed",
                endpoint=None,
                kubernetes_detail=None,
                message=(
                    f"MAGI {spec.magic_id} failed health check on port "
                    f"{port} within {HEALTH_POLL_TIMEOUT_S:.0f}s"
                ),
            )
        base_url = f"http://127.0.0.1:{port}"
        if self._control is not None:
            try:
                self._control.record_spawn(spec.magic_id, pid, base_url, port)
            except Exception:
                logger.exception(
                    "control_registry.record_spawn failed",
                    extra={"runtime_id": spec.magic_id, "pid": pid},
                )
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="cli",
            backend_ref=backend_ref,
            observed_state="running",
            endpoint=RuntimeEndpoint(
                runtime_id=spec.magic_id,
                backend_kind="cli",
                base_url=base_url,
                backend_ref=backend_ref,
                observed_state="running",
            ),
            kubernetes_detail=None,
            message=f"MAGI {spec.magic_id} started (PID={pid})",
        )

    def stop(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        pid = self._lookup_pid(spec.magic_id)
        if pid is not None:
            self._signal_graceful(pid)
        if self._control is not None:
            try:
                self._control.record_stop(spec.magic_id)
            except Exception:
                logger.exception(
                    "control_registry.record_stop failed",
                    extra={"runtime_id": spec.magic_id},
                )
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="cli",
            backend_ref=f"cli://{pid if pid is not None else '?'}",
            observed_state="stopped",
            endpoint=None,
            kubernetes_detail=None,
            message="stopped; port reservation retained (delete to release)",
        )

    def delete(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        pid = self._lookup_pid(spec.magic_id)
        if pid is not None and self._is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self._control is not None:
            try:
                self._control.release_port(spec.magic_id)
            except Exception:
                logger.exception(
                    "control_registry.release_port failed",
                    extra={"runtime_id": spec.magic_id},
                )
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="cli",
            backend_ref=f"cli://{pid if pid is not None else '?'}",
            observed_state="deleted",
            endpoint=None,
            kubernetes_detail=None,
            message="CLI Profile — workspace preserved; port released",
        )

    def endpoint_for(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        if self._control is not None:
            try:
                row = self._control.get_runtime(spec.magic_id)
            except Exception:
                row = None
            if row is not None and row.base_url:
                observed = (
                    row.observed_state.value
                    if hasattr(row.observed_state, "value")
                    else str(row.observed_state)
                )
                backend_ref = row.backend_ref or f"cli://{row.pid or '?'}"
                return RuntimeOperationResult(
                    runtime_id=spec.magic_id,
                    backend_kind="cli",
                    backend_ref=backend_ref,
                    observed_state=observed,
                    endpoint=RuntimeEndpoint(
                        runtime_id=spec.magic_id,
                        backend_kind="cli",
                        base_url=row.base_url,
                        backend_ref=backend_ref,
                        observed_state=observed,
                    ),
                    kubernetes_detail=None,
                    message="endpoint from control registry",
                )
        port = int(os.environ.get("MAGI_PORT", str(DEFAULT_PORT)))
        backend_ref = f"cli://{os.getpid()}"
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="cli",
            backend_ref=backend_ref,
            observed_state="unknown",
            endpoint=RuntimeEndpoint(
                runtime_id=spec.magic_id,
                backend_kind="cli",
                base_url=f"http://127.0.0.1:{port}",
                backend_ref=backend_ref,
                observed_state="unknown",
            ),
            kubernetes_detail=None,
            message="control registry unavailable; inferring from env",
        )

    # ── helpers ──────────────────────────────────────────────────────── #

    def _allocate_port(self, magic_id: int) -> int:
        if self._control is not None:
            try:
                return self._control.allocate_port(magic_id).port
            except Exception:
                logger.exception(
                    "control_registry.allocate_port failed; falling back to env",
                    extra={"runtime_id": magic_id},
                )
        return int(os.environ.get("MAGI_PORT", str(DEFAULT_PORT)))

    def _spawn(self, spec: RuntimeSpec, port: int) -> tuple[str, int]:
        from magi.launcher.paths import (
            magis_db_path,
            runtime_workspace_root,
        )

        data_root = _data_root()
        slug = _resolve_slug(spec, data_root)
        ws_root = runtime_workspace_root(data_root, spec.magic_id, slug)
        ws_root.mkdir(parents=True, exist_ok=True)

        magis_id = spec.magis_id or 1
        magis_slug = spec.magis_name or "genesis"
        magis_db = magis_db_path(data_root, magis_id, magis_slug)

        env = os.environ.copy()
        env.update(
            {
                "HOST_WORKSPACE_DIR": str(data_root),
                "MAGI_WORKSPACE_DIR": str(ws_root),
                "MAGI_RUNTIME_ID": str(spec.magic_id),
                "MAGI_NAME": slug,
                "MAGIS_DATABASE_URL": f"sqlite:///{magis_db}",
                "MAGI_PORT": str(port),
                "MAGI_BACKEND": "cli",
            }
        )

        argv = [
            sys.executable,
            "-m",
            "magi",
            "runtime",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]

        from magi.launcher.platform import current_platform

        popen_kwargs: dict = {"env": env}
        if current_platform() != "windows":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )

        logger.info(
            "spawning MAGI subprocess",
            extra={
                "argv": argv,
                "data_root": str(data_root),
                "ws_root": str(ws_root),
                "port": port,
                "magic_id": spec.magic_id,
                "slug": slug,
            },
        )
        proc = subprocess.Popen(argv, **popen_kwargs)
        return f"cli://{proc.pid}", proc.pid

    def _wait_healthy(self, port: int) -> bool:
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

    def _lookup_pid(self, magic_id: int) -> Optional[int]:
        if self._control is None:
            return None
        try:
            row = self._control.get_runtime(magic_id)
        except Exception:
            return None
        return row.pid

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _signal_graceful(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + STOP_GRACE_S
        while time.monotonic() < deadline:
            if not self._is_alive(pid):
                return
            time.sleep(STOP_POLL_INTERVAL_S)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ── module-level helpers ───────────────────────────────────────────── #


def _data_root() -> Path:
    from magi.launcher.paths import default_data_root

    raw = os.environ.get("HOST_WORKSPACE_DIR")
    return Path(raw) if raw else default_data_root()


def _resolve_slug(spec: RuntimeSpec, data_root: Path) -> str:
    """Pick a directory-safe slug for the new runtime.

    Honours an explicit ``spec.name``; otherwise derives ``eva-NNN`` from
    the magic_id.  Falls back to ``eva`` if the directory already
    contains a slot with the same name (rare — the launcher resolves
    conflicts upstream).
    """
    if spec.name:
        return spec.name
    return f"eva-{spec.magic_id:03d}"


import logging

logger = logging.getLogger("magi.orchestrator.backends.cli_process")


__all__ = ["CLIProcessRuntimeBackend"]
