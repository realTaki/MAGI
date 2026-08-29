"""Orchestrator wire contracts — Pydantic DTOs shared between ADAM and the orchestrator service.

Consolidated from the legacy ``magi.orchestrator.contracts`` module
per plan §20.4. The orchestrator service lives in
:mod:`magi.startup.kubernetes.service`; the ADAM-side client lives
in :mod:`magi.startup.kubernetes.client`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MagisBinding(BaseModel):
    """The MAGI's one direct MAGIS runtime binding."""

    id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)


class MagisRuntimeConfiguration(BaseModel):
    """Configuration projected by control plane into a direct MAGIS DB."""

    magis_instruction: str = Field(default="", max_length=12000)
    role_name: str = Field(min_length=1, max_length=80)
    role_instruction: str = Field(default="", max_length=12000)
    magi_name: str | None = Field(default=None, max_length=100)
    personal_instruction: str = Field(default="", max_length=12000)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


class EvaSpec(BaseModel):
    magi_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=100)
    magis: MagisBinding | None = None
    configuration: MagisRuntimeConfiguration | None = None


class EvaOperationResult(BaseModel):
    observed_state: str
    namespace: str
    deployment_name: str
    workspace_claim_name: str
    credential_secret_name: str | None = None
    message: str | None = None


class MagisProvisionResult(BaseModel):
    magis_id: int | None = Field(default=None, ge=1)
    backend_kind: str = "kubernetes"
    database_service_name: str
    workspace_claim_name: str
    message: str | None = None


class RuntimeEndpoint(BaseModel):
    """Platform-neutral runtime location exposed by the control plane."""

    runtime_id: int = Field(ge=1)
    backend_kind: str
    base_url: str
    backend_ref: str
    observed_state: str


class RuntimeSpec(BaseModel):
    """Identity needed for one runtime lifecycle operation."""

    magi_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=100)
    magis_id: int | None = Field(default=None, ge=1)
    magis_name: str | None = Field(default=None, max_length=120)


class RuntimeOperationResult(BaseModel):
    runtime_id: int = Field(ge=1)
    backend_kind: str
    backend_ref: str
    observed_state: str
    endpoint: RuntimeEndpoint | None = None
    kubernetes_detail: dict | None = None
    message: str | None = None


__all__ = [
    "MagisBinding",
    "MagisRuntimeConfiguration",
    "EvaSpec",
    "EvaOperationResult",
    "MagisProvisionResult",
    "RuntimeEndpoint",
    "RuntimeOperationResult",
    "RuntimeSpec",
]
