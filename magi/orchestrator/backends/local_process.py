"""``LocalProcessRuntimeBackend`` — Local Profile runtime lifecycle.

Per plan §7.1 the Local backend is responsible for:

- Translating :class:`RuntimeSpec` into a :class:`ProcessSpec` (so the
  backend never sees raw subprocess details).
- Calling the supervisor (:mod:`magi.launcher.supervisor`) to spawn /
  stop / delete / inspect.
- Persisting desired / observed state through
  :class:`magi.bus.services.control_registry.ControlRegistryService`
  — never the control ORM directly (plan §6.1: backend talks to BUS,
  BUS owns storage).

The backend never opens DB sessions, never writes files outside the
supervisor's carving, and never sets ``shell=True`` (plan §7.1 argv
rule).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from magi.bus.contracts.lifecycle import (
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)
from magi.bus.contracts.runtime import RuntimeEndpoint
from magi.bus.services.control_registry import (
    ControlRegistryService,
    RuntimeDesiredState,
    RuntimeObservedState,
)
from magi.launcher.ports import reserve_port
from magi.launcher.supervisor import (
    ProcessSpec,
    ProcessSupervisor,
    pid_alive,
)

logger = logging.getLogger("magi.orchestrator.backends.local_process")


class LocalProcessRuntimeBackend:
    """``RuntimeBackend`` implementation rooted at supervisor + BUS."""

    kind = "local_process"

    def __init__(self, control: Optional[ControlRegistryService] = None) -> None:
        if control is None:
            # Lazy resolve when the dispatcher is constructed inside a
            # fully-bootstrapped Bus (Phase 4/5 production wiring).
            from magi.bus import get_bus

            control = get_bus().control_registry
        if control is None:
            raise RuntimeError(
                "LocalProcessRuntimeBackend requires bus.control_registry; "
                "did bootstrap_local() build one?"
            )
        self._control = control
        self._supervisor = ProcessSupervisor(control)

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _slug_for(spec: RuntimeSpec) -> str:
        """Stable slug from ``spec.name`` or ``magis_name``-derived fallback."""
        source = (spec.name or spec.magis_name or f"runtime-{spec.magic_id}").strip().lower()
        out = []
        for ch in source:
            if ch.isalnum() or ch in "-_.":
                out.append(ch)
            elif ch in " /":
                out.append("-")
        return "".join(out)[:80] or f"runtime-{spec.magic_id}"

    def _build_argv(self, spec: RuntimeSpec, port: int) -> list[str]:
        """Compose the runtime subprocess argv (plan §7.1 — argv array, no shell)."""
        # ``-m magi`` keeps the launcher Python-version-aligned and
        # keeps argv opaque enough that supervisor can inspect PIDs.
        return [
            sys.executable,
            "-m",
            "magi",
            "runtime",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]

    def _persist_paths(self, spec: RuntimeSpec, slug: str) -> None:
        from magi.launcher.paths import (
            default_data_root,
            runtime_audit_log_path,
            runtime_log_dir,
            runtime_workspace_root,
        )

        data_root = default_data_root()
        ws = runtime_workspace_root(data_root, spec.magic_id, slug)
        log_d = runtime_log_dir(data_root, spec.magic_id, slug)
        audit_p = runtime_audit_log_path(data_root, spec.magic_id, slug)
        self._control.attach_paths(
            spec.magic_id,
            workspace_dir=ws,
            log_dir=log_d,
            audit_log_path=audit_p,
            backend_ref=f"local-{slug}",
        )
        self._control.upsert_desired_state(
            spec.magic_id, "local_process", RuntimeDesiredState.STARTED
        )

    # -- RuntimeBackend Protocol ------------------------------------------

    def provision_magis(self, magis_id: int, magis_name: str) -> MagisProvisionResult:
        """Local Profile already has the per-MAGIS SQLite from Phase 3.

        No backend-side resources to provision — the Composition Root
        builds the file layout during ``bootstrap_local`` and Phase 4
        just confirms it.  Returns the standard result with K8s-
        specific fields empty.
        """
        return MagisProvisionResult(
            magis_id=magis_id,
            backend_kind="local_process",
            database_service_name=None,
            workspace_claim_name=None,
            message=(
                f"Local Profile: per-MAGIS SQLite is provisioned by "
                f"bootstrap_local() (plan §6.1)."
            ),
        )

    def start(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        slug = self._slug_for(spec)
        # Idempotent: if the runtime is already STARTED and alive,
        # return the existing endpoint.
        try:
            existing = self._control.get_runtime(spec.magic_id)
            if (
                existing.observed_state == RuntimeObservedState.STARTED
                and existing.port is not None
                and existing.pid is not None
                and pid_alive(existing.pid)
            ):
                return self._result_started(existing.port, spec)
        except Exception:
            existing = None  # noqa: F841 — fresh start path

        alloc = reserve_port(self._control, spec.magic_id)
        self._persist_paths(spec, slug)
        handle = self._supervisor.spawn(
            ProcessSpec(
                runtime_id=spec.magic_id,
                slug=slug,
                argv=self._build_argv(spec, alloc.port),
                env={
                    "MAGI_WORKSPACE_DIR": str(Path("/tmp").resolve()),  # runtime derives state
                    "MAGI_RUNTIME_PORT": str(alloc.port),
                    "MAGI_RUNTIME_SLUG": slug,
                },
            )
        )
        base_url = f"http://127.0.0.1:{alloc.port}"
        self._control.record_spawn(spec.magic_id, handle.pid, base_url, alloc.port)
        # Plan §7.1 — health-check probe via /health (BOS provided by
        # the runtime subprocess).  Best-effort non-blocking; we don't
        # wait synchronously in start() per plan §8.
        return self._result_started(alloc.port, spec)

    def stop(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        self._supervisor.stop(spec.magic_id)
        self._control.record_stop(spec.magic_id)
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="local_process",
            backend_ref=f"local-{self._slug_for(spec)}",
            observed_state="stopped",
            endpoint=None,
            message="stopped via LocalProcessRuntimeBackend",
        )

    def delete(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        slug = self._slug_for(spec)
        # Stop first to make PID-stale recovery unambiguous.
        self._supervisor.stop(spec.magic_id)
        from magi.launcher.paths import (
            default_data_root,
            runtime_workspace_root,
        )

        ws = runtime_workspace_root(default_data_root(), spec.magic_id, slug)
        archive = ws.parent / "archive" / slug
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            if ws.exists():
                ws.rename(archive)
        except OSError:
            # Best-effort archive; forensically recoverable from the
            # workspace itself if rename fails on Windows.
            archive = ws
        self._control.archive_workspace(spec.magic_id, archive)
        self._control.forget(spec.magic_id)
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="local_process",
            backend_ref=f"local-{slug}",
            observed_state="deleted",
            endpoint=None,
            message=f"workspace archived to {archive}",
        )

    def endpoint_for(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        try:
            row = self._control.get_runtime(spec.magic_id)
        except Exception:
            row = None
        endpoint: Optional[RuntimeEndpoint] = None
        if row is not None and row.base_url:
            endpoint = RuntimeEndpoint(
                runtime_id=spec.magic_id,
                backend_kind="local_process",
                base_url=row.base_url,
                backend_ref=row.backend_ref,
                observed_state=row.observed_state.value,
            )
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="local_process",
            backend_ref="",
            observed_state=row.observed_state.value if row else "unknown",
            endpoint=endpoint,
        )

    # -- private result helpers -------------------------------------------

    def _result_started(self, port: int, spec: RuntimeSpec) -> RuntimeOperationResult:
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="local_process",
            backend_ref=f"local-{self._slug_for(spec)}",
            observed_state="started",
            endpoint=RuntimeEndpoint(
                runtime_id=spec.magic_id,
                backend_kind="local_process",
                base_url=f"http://127.0.0.1:{port}",
                backend_ref=f"local-{self._slug_for(spec)}",
                observed_state="started",
            ),
        )


__all__ = ["LocalProcessRuntimeBackend"]
