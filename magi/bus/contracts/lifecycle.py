"""Platform-neutral Runtime lifecycle DTOs.

These replace the K8s-flavored fields in
:class:`magi.orchestrator.contracts.EvaOperationResult` /
:class:`magi.orchestrator.contracts.MagisProvisionResult`.  The
:class:`KubernetesBackendDetail` nested DTO carries the legacy fields
when the adapter needs to surface them (today, every backend; in
Phase 4, only the K8s adapter).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from magi.bus.contracts.runtime import BackendKind, RuntimeEndpoint


class RuntimeSpec(BaseModel):
    """Specification of one runtime lifecycle operation.

    Replaces :class:`magi.orchestrator.contracts.EvaSpec`.  Phase 2 keeps
    ``magic_id`` as the primary identifier; the optional
    ``magis_id`` / ``magis_name`` pair is required for ``start`` so the
    backend can wire the new runtime to its direct MAGIS.
    """

    magic_id: int = Field(ge=1)
    name: Optional[str] = Field(default=None, max_length=100)
    magis_id: Optional[int] = Field(default=None, ge=1)
    magis_name: Optional[str] = Field(default=None, max_length=120)


class KubernetesBackendDetail(BaseModel):
    """Legacy K8s-specific fields surfaced for backward compatibility.

    Only the K8s backend populates this; the Local backend (Phase 4)
    leaves it ``None``.  Callers must not branch on these fields —
    they're a diagnostic surface, not a control plane.
    """

    namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    workspace_claim_name: Optional[str] = None
    credential_secret_name: Optional[str] = None


class RuntimeOperationResult(BaseModel):
    """Platform-neutral result of a lifecycle operation.

    Replaces :class:`magi.orchestrator.contracts.EvaOperationResult`.
    K8s-specific detail migrates into ``kubernetes_detail`` (optional).
    """

    runtime_id: int = Field(ge=1)
    backend_kind: BackendKind
    backend_ref: str = Field(min_length=1)
    observed_state: str = Field(min_length=1)
    endpoint: Optional[RuntimeEndpoint] = None
    kubernetes_detail: Optional[KubernetesBackendDetail] = None
    message: Optional[str] = None


class MagisProvisionResult(BaseModel):
    """Platform-neutral result of provisioning one MAGIS.

    Today, K8s is the only backend that creates per-MAGIS resources
    (PostgreSQL deployment, PVC, Service).  Local Profile keeps
    ``database_service_name`` / ``workspace_claim_name`` as ``None`` and
    sets ``message`` to a description of the SQLite-backed layout.
    """

    magis_id: int = Field(ge=1)
    backend_kind: BackendKind
    database_service_name: Optional[str] = None
    workspace_claim_name: Optional[str] = None
    message: Optional[str] = None


__all__ = [
    "RuntimeSpec",
    "RuntimeOperationResult",
    "MagisProvisionResult",
    "KubernetesBackendDetail",
]