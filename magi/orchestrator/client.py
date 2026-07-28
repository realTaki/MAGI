"""Adam-side client for the restricted orchestrator API."""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import httpx

from magi.orchestrator.contracts import EveOperationResult, EveSpec


class OrchestratorUnavailable(RuntimeError):
    """The lifecycle controller could not accept an operation."""


def _control_secret() -> str:
    value = os.environ.get("MAGI_CONTROL_SECRET")
    if not value:
        raise OrchestratorUnavailable("MAGI_CONTROL_SECRET is not configured")
    return value


def _headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        _control_secret().encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {"X-MAGI-Timestamp": timestamp, "X-MAGI-Signature": digest}


def request_lifecycle(action: str, spec: EveSpec) -> EveOperationResult:
    """Request a start or stop without exposing Kubernetes credentials to Adam."""
    if action not in {"start", "stop", "delete"}:
        raise ValueError(f"unsupported EVE lifecycle action: {action}")
    url = os.environ.get("MAGI_ORCHESTRATOR_URL", "http://magi-orchestrator:42100")
    body = spec.model_dump_json(exclude_none=True).encode()
    try:
        response = httpx.post(
            f"{url.rstrip('/')}/v1/eves/{spec.magi_id}/{action}",
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
    return EveOperationResult.model_validate(response.json())
