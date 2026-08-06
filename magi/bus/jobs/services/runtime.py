"""Platform-neutral Runtime lifecycle + registry services.

- :class:`BackendDispatcherService` — the seam through which business
  modules start / stop / delete a runtime.  With the unified startup
  refactor (:mod:`magi.startup`), the backend abstraction has been
  retired — local process management lives in :mod:`magi.startup.local`,
  K8s resource creation in :mod:`magi.startup.kubernetes`.

- :class:`RuntimeRegistryService` — resolves a
  :class:`~magi.bus.jobs.protocols.runtime.RuntimeEndpoint` for a magic_id,
  replacing the legacy ``f"http://{deployment_name}:42069"`` URL forging
  done at :mod:`magi.channels.api.runtime_proxy`.

Both services are constructed by :func:`magi.bus.bootstrap` and exposed
on the :class:`magi.bus.Bus` facade as ``bus.runtime`` and
``bus.registry``.  No business module instantiates them directly.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.bus.jobs.protocols.lifecycle import (
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)
from magi.bus.jobs.protocols.runtime import RuntimeEndpoint

logger = logging.getLogger("magi.bus.jobs.services.runtime")


class OrchestratorUnavailable(RuntimeError):
    """Raised when the lifecycle controller could not accept an operation.

    Re-exported from the BUS layer so API handlers can catch a
    BUS-defined exception instead of reaching into the orchestrator
    package.  The orchestrator client raises its own
    ``OrchestratorUnavailable``; the dispatcher wraps it as this type
    before propagating.
    """


class BackendDispatcherService:
    """BUS facade for runtime lifecycle commands.

    In Phase 2 this is an in-process shim that calls the active
    :class:`~magi.orchestrator.backends.base.RuntimeBackend` directly.
    Phase 4-7 substitutes the body with a real BUS command queue so the
    Orchestrator Worker can consume commands asynchronously.  Tests can
    inject a stub backend via the constructor.
    """

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend

    @property
    def backend(self) -> Any:
        """Resolve the backend on demand.

        With the unified startup refactor, local process management is
        handled by :mod:`magi.startup.local` and K8s resources by
        :mod:`magi.startup.kubernetes`.  The legacy backend factory has
        been retired; this property returns ``None`` and callers should
        use the new startup modules instead.
        """
        return self._backend

    def provision_magis(self, magis_id: int, magis_name: str) -> MagisProvisionResult:
        """Provision one MAGIS's public database + workspace."""
        result = self.backend.provision_magis(magis_id=magis_id, magis_name=magis_name)
        logger.info(
            "magis provision dispatched",
            extra={"magis_id": magis_id, "backend_kind": result.backend_kind},
        )
        return result

    def start(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Bring one runtime up; idempotent."""
        try:
            result = self.backend.start(spec)
        except OrchestratorUnavailable:
            raise
        except RuntimeError as exc:
            # Wrap backend-level RuntimeError as the BUS-defined
            # ``OrchestratorUnavailable`` so API handlers can catch one
            # exception type without reaching into the orchestrator
            # package.
            raise OrchestratorUnavailable(str(exc)) from exc
        logger.info(
            "runtime start dispatched",
            extra={
                "runtime_id": spec.magic_id,
                "backend_kind": result.backend_kind,
                "observed_state": result.observed_state,
            },
        )
        return result

    def stop(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Stop one runtime; state / data preserved."""
        try:
            result = self.backend.stop(spec)
        except OrchestratorUnavailable:
            raise
        except RuntimeError as exc:
            raise OrchestratorUnavailable(str(exc)) from exc
        logger.info(
            "runtime stop dispatched",
            extra={
                "runtime_id": spec.magic_id,
                "backend_kind": result.backend_kind,
                "observed_state": result.observed_state,
            },
        )
        return result

    def delete(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Remove one runtime's deployment resources."""
        try:
            result = self.backend.delete(spec)
        except OrchestratorUnavailable:
            raise
        except RuntimeError as exc:
            raise OrchestratorUnavailable(str(exc)) from exc
        logger.info(
            "runtime delete dispatched",
            extra={
                "runtime_id": spec.magic_id,
                "backend_kind": result.backend_kind,
                "observed_state": result.observed_state,
            },
        )
        return result

    def endpoint_for(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Return the current observed endpoint without changing state."""
        return self.backend.endpoint_for(spec)


class RuntimeRegistryService:
    """BUS facade for resolving :class:`RuntimeEndpoint` for a magic_id.

    Phase 2 derives the endpoint from the legacy
    :class:`~magi.bus.db.models.magis.eva_runtime.EvaRuntime` row when
    present, falling back to the dispatcher's ``endpoint_for`` query.
    Phase 4 will replace the implementation with a Local-registry
    table (``magi.db.control``); the public API stays stable.
    """

    def __init__(self, dispatcher: BackendDispatcherService | None = None) -> None:
        self._dispatcher = dispatcher or BackendDispatcherService()

    def _legacy_endpoint(self, magic_id: int) -> RuntimeEndpoint | None:
        try:
            from magi.bus.db.magis import open_magis_session
            from magi.bus.db.models.magis.eva_runtime import EvaRuntime

            with open_magis_session() as session:
                runtime = (
                    session.query(EvaRuntime)
                    .filter(EvaRuntime.magic_id == magic_id)
                    .one_or_none()
                )
            if runtime is None or not runtime.deployment_name:
                return None
            observed = str(runtime.observed_state or "unknown")
            return RuntimeEndpoint(
                runtime_id=magic_id,
                backend_kind="kubernetes",
                base_url=f"http://{runtime.deployment_name}:42069",
                backend_ref=runtime.deployment_name,
                observed_state=observed,
            )
        except Exception:  # noqa: BLE001 — registry is best-effort
            logger.debug("legacy endpoint lookup failed", exc_info=True)
            return None

    def _local_endpoint(self, magic_id: int) -> RuntimeEndpoint | None:
        """Phase 7 — read the Local control-registry row before forging URLs."""
        from magi.bus.jobs.services.control_registry import ControlRegistryService

        try:
            from magi.bus import get_bus
            bus = get_bus()
        except Exception:
            return None
        control: Optional[ControlRegistryService] = getattr(bus, "control_registry", None)
        if control is None:
            return None
        try:
            row = control.get_runtime(magic_id)
        except Exception:
            return None
        if row.base_url is None:
            return None
        return RuntimeEndpoint(
            runtime_id=magic_id,
            backend_kind="cli",
            base_url=row.base_url,
            backend_ref=row.backend_ref,
            observed_state=row.observed_state.value,
        )

    def resolve_endpoint(self, magic_id: int) -> RuntimeEndpoint | None:
        """Return the platform-neutral endpoint for ``magic_id``.

        Returns ``None`` when the runtime isn't registered (e.g. before
        the first ``start`` call).  Callers that need to send HTTP
        traffic must treat ``None`` as "not yet running" and surface a
        409 / 503 to the operator.

        Phase 7 — Local Profile is checked first when present,
        because the Local control registry is the authoritative source
        for live ``base_url`` values.  K8s Profile falls back to the
        legacy ``eva_runtime.deployment_name`` URL forging.
        """
        local = self._local_endpoint(magic_id)
        if local is not None and local.observed_state not in {"stopped", "deleted"}:
            return local
        legacy = self._legacy_endpoint(magic_id)
        if legacy is not None and legacy.observed_state not in {"stopped", "deleted"}:
            return legacy
        # Last-resort: ask the backend directly.  For K8s this returns
        # the deployment-name URL even if the Pod isn't yet running;
        # callers must not assume HTTP reachability.
        try:
            result = self._dispatcher.endpoint_for(RuntimeSpec(magic_id=magic_id))
        except Exception:  # noqa: BLE001 — registry is best-effort
            logger.debug("backend endpoint_for lookup failed", exc_info=True)
            return legacy
        return result.endpoint


__all__ = ["BackendDispatcherService", "RuntimeRegistryService", "OrchestratorUnavailable"]