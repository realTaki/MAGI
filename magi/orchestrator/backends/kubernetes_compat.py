"""Kubernetes backend adapter.

Wraps the legacy :class:`magi.orchestrator.kubernetes.KubernetesEvaBackend`
and exposes the platform-neutral :class:`RuntimeBackend` Protocol.  The
legacy class stays in :mod:`magi.orchestrator.kubernetes` until Phase 9
(per plan §4.2); this adapter translates between old and new DTOs so
the BUS never sees K8s-shaped fields.

This file is the **only** place the runtime/lifecycle result types are
populated with K8s-specific fields; every other BUS consumer sees only
the :class:`~magi.bus.jobs.protocols.runtime.RuntimeEndpoint` /
:class:`~magi.bus.jobs.protocols.lifecycle.RuntimeOperationResult` surface.
"""

from __future__ import annotations

import re

from magi.bus.jobs.protocols.lifecycle import (
    KubernetesBackendDetail,
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)
from magi.bus.jobs.protocols.runtime import RuntimeEndpoint
from magi.orchestrator.contracts import MagisBinding
from magi.orchestrator.kubernetes import KubernetesEvaBackend


def _slug(value: str | None, fallback: str) -> str:
    raw = (value or fallback).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or fallback
    return slug[:48].rstrip("-")


class KubernetesEvaBackendAdapter:
    """``RuntimeBackend`` implementation backed by Kubernetes."""

    kind = "kubernetes"

    def __init__(self, inner: KubernetesEvaBackend | None = None) -> None:
        # Default to the legacy class so the behaviour is bit-identical
        # in Phase 2; tests can inject a stub.
        self._inner = inner or KubernetesEvaBackend()

    def provision_magis(self, magis_id: int, magis_name: str) -> MagisProvisionResult:
        legacy = self._inner.provision_magis(MagisBinding(id=magis_id, name=magis_name))
        return MagisProvisionResult(
            magis_id=magis_id,
            backend_kind="kubernetes",
            database_service_name=legacy.database_service_name,
            workspace_claim_name=legacy.workspace_claim_name,
            message=legacy.message,
        )

    def _resolve(self, spec: RuntimeSpec) -> tuple[str, MagisBinding | None]:
        name = _slug(spec.name or "eva", "eva")
        deployment_name = f"magi-eva-{spec.magic_id}-{name}"[:63].rstrip("-")
        binding = (
            MagisBinding(id=spec.magis_id, name=spec.magis_name or f"magis-{spec.magis_id}")
            if spec.magis_id is not None
            else None
        )
        return deployment_name, binding

    def _to_result(
        self,
        legacy,
        spec: RuntimeSpec,
        _deployment_name: str,
    ) -> RuntimeOperationResult:
        endpoint = None
        if legacy.observed_state not in {"stopped", "deleted"} and legacy.deployment_name:
            endpoint = RuntimeEndpoint(
                runtime_id=spec.magic_id,
                backend_kind="kubernetes",
                base_url=f"http://{legacy.deployment_name}:42069",
                backend_ref=legacy.deployment_name,
                observed_state=legacy.observed_state,
            )
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="kubernetes",
            backend_ref=legacy.deployment_name,
            observed_state=legacy.observed_state,
            endpoint=endpoint,
            kubernetes_detail=KubernetesBackendDetail(
                namespace=legacy.namespace,
                deployment_name=legacy.deployment_name,
                workspace_claim_name=legacy.workspace_claim_name,
                credential_secret_name=legacy.credential_secret_name,
            ),
            message=legacy.message,
        )

    def _spec(self, magic_id: int, name: str | None) -> RuntimeSpec:
        return RuntimeSpec(magic_id=magic_id, name=name)

    def start(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        deployment_name, _binding = self._resolve(spec)
        # Reuse the legacy adapter's path: it self-resolves slug from
        # spec.name.  Phase 2 keeps ``magis`` out of the BUS in the
        # DTO body because the legacy backend writes its own MAGIS
        # projection when ``spec.magis`` is provided.  We pass a thin
        # EvaSpec shim through the legacy constructor so the
        # Kubernetes manifest is identical.
        from magi.orchestrator.contracts import EvaSpec, MagisBinding, MagisRuntimeConfiguration

        magis = (
            MagisBinding(id=spec.magis_id, name=spec.magis_name or f"magis-{spec.magis_id}")
            if spec.magis_id is not None
            else None
        )
        legacy = self._inner.start(
            EvaSpec(
                magic_id=spec.magic_id,
                name=spec.name,
                magis=magis,
                configuration=None,
            ),
        )
        return self._to_result(legacy, spec, deployment_name)

    def stop(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        from magi.orchestrator.contracts import EvaSpec

        deployment_name, _ = self._resolve(spec)
        legacy = self._inner.stop(EvaSpec(magic_id=spec.magic_id, name=spec.name))
        return self._to_result(legacy, spec, deployment_name)

    def delete(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        from magi.orchestrator.contracts import EvaSpec

        deployment_name, _ = self._resolve(spec)
        legacy = self._inner.delete(EvaSpec(magic_id=spec.magic_id, name=spec.name))
        return self._to_result(legacy, spec, deployment_name)

    def endpoint_for(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        deployment_name, _ = self._resolve(spec)
        return RuntimeOperationResult(
            runtime_id=spec.magic_id,
            backend_kind="kubernetes",
            backend_ref=deployment_name,
            observed_state="unknown",
            endpoint=RuntimeEndpoint(
                runtime_id=spec.magic_id,
                backend_kind="kubernetes",
                base_url=f"http://{deployment_name}:42069",
                backend_ref=deployment_name,
                observed_state="unknown",
            ),
        )


__all__ = ["KubernetesEvaBackendAdapter"]