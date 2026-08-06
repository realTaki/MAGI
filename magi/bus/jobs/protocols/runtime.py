"""Platform-neutral Runtime contracts.

These DTOs replace the K8s-flavored fields that previously leaked into the
BUS facade (``namespace``, ``deployment_name``, ``workspace_claim_name``,
``credential_secret_name``).  Per plan §4.3 the public API carries only
platform-neutral fields; the legacy fields stay reachable via the
optional :class:`~magi.bus.jobs.protocols.lifecycle.KubernetesBackendDetail`
nested DTO for backward compatibility with the K8s adapter.

BUS services (``magi.bus.jobs.services.runtime.RuntimeRegistryService``,
``magi.bus.jobs.services.runtime.BackendDispatcherService``) return these
types.  The orchestrator package wraps the legacy
:class:`magi.orchestrator.kubernetes.KubernetesEvaBackend` and translates
its results into these DTOs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


BackendKind = Literal["kubernetes", "cli"]
"""Supported backend identifiers.  ``cli`` is the per-MAGI standalone process."""


class RuntimeEndpoint(BaseModel):
    """Platform-neutral descriptor of one running MAGI runtime.

    Replaces the legacy ``f"http://{deployment_name}:42069"`` URL forging
    done at :mod:`magi.channels.api.runtime_proxy`.  ``base_url`` is the
    single source of truth for any HTTP client that needs to reach this
    runtime; ``backend_ref`` is the backend-specific handle (K8s Service
    name, CLI PID, etc.) surfaced for diagnostics only.
    """

    runtime_id: int = Field(ge=1)
    backend_kind: BackendKind
    base_url: str = Field(min_length=1)
    backend_ref: str = Field(min_length=1)
    observed_state: str = Field(min_length=1)


__all__ = ["BackendKind", "RuntimeEndpoint"]