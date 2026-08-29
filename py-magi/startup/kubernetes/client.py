"""ADAM-side client for the restricted orchestrator API.

Consolidated from the legacy ``magi.orchestrator.client`` module
per plan §20.4. Talks to the FastAPI service in
:mod:`startup.kubernetes.service` over HMAC-signed HTTP.

The HMAC key is resolved at call time by :mod:`startup.kubernetes._secret`,
which prefers the DB-backed ``control_secrets`` row and falls back to the
``MAGI_CONTROL_SECRET`` env var as a deployment-side bootstrap bridge
(for the cluster-init phase that creates the MAGIS database).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import httpx

from startup.kubernetes._secret import get_control_secret
from startup.kubernetes.contracts import (
    EvaOperationResult,
    EvaSpec,
    MagisBinding,
    MagisProvisionResult,
)


class OrchestratorUnavailable(RuntimeError):
    """The lifecycle controller could not accept an operation."""


def _control_secret() -> bytes:
    secret = get_control_secret()
    if not secret:
        raise OrchestratorUnavailable(
            "control secret is not configured (neither MAGIS_DATABASE_URL "
            "control_secrets row nor MAGI_CONTROL_SECRET env var); "
            "ensure the cluster bootstrap has run."
        )
    return secret


def _headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        _control_secret(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {"X-MAGI-Timestamp": timestamp, "X-MAGI-Signature": digest}


def request_lifecycle(action: str, spec: EvaSpec) -> EvaOperationResult:
    """Request a start or stop without exposing Kubernetes credentials to ADAM."""
    if action not in {"start", "stop", "delete"}:
        raise ValueError(f"unsupported EVA lifecycle action: {action}")
    url = os.environ.get("MAGI_ORCHESTRATOR_URL", "http://magi-orchestrator:42100")
    body = spec.model_dump_json(exclude_none=True).encode()
    try:
        response = httpx.post(
            f"{url.rstrip('/')}/v1/evas/{spec.magi_id}/{action}",
            content=body,
            headers={"content-type": "application/json", **_headers(body)},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise OrchestratorUnavailable(f"orchestrator request failed: {exc}") from exc
    if response.status_code >= 400:
        raise OrchestratorUnavailable(
            f"orchestrator rejected {action}: {response.status_code} {response.text[:500]}"
        )
    return EvaOperationResult.model_validate(response.json())


def provision_magis(binding: MagisBinding) -> MagisProvisionResult:
    """Create the public database and workspace for a MAGIS through control plane."""
    url = os.environ.get("MAGI_ORCHESTRATOR_URL", "http://magi-orchestrator:42100")
    body = binding.model_dump_json().encode()
    try:
        response = httpx.post(
            f"{url.rstrip('/')}/v1/magis/{binding.id}/provision",
            content=body,
            headers={"content-type": "application/json", **_headers(body)},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise OrchestratorUnavailable(f"MAGIS provision request failed: {exc}") from exc
    if response.status_code >= 400:
        raise OrchestratorUnavailable(
            f"orchestrator rejected MAGIS provision: {response.status_code} {response.text[:500]}"
        )
    return MagisProvisionResult.model_validate(response.json())


__all__ = [
    "OrchestratorUnavailable",
    "request_lifecycle",
    "provision_magis",
]
