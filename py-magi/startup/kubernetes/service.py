"""Authenticated FastAPI service that owns EVA Kubernetes operations.

Consolidated from the legacy ``magi.orchestrator.service`` module
per plan §20.4. Per plan §6 — there is no Backend abstraction layer;
the service uses the Kubernetes path directly via the in-cluster
:class:`startup.kubernetes.resources.KubernetesEvaBackend` and the
``KubernetesEvaBackend`` semantics that wrap it.

The HMAC key for verifying ``X-MAGI-Timestamp`` / ``X-MAGI-Signature``
is resolved by :mod:`startup.kubernetes._secret`.  See that
module for the env-var → DB fallback order.

Run via:

    uvicorn startup.kubernetes.service:create_app --factory --host 0.0.0.0 --port 42100
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import FastAPI, Header, HTTPException, Request

from startup.kubernetes._secret import get_control_secret
from startup.kubernetes.contracts import (
    EvaSpec,
    MagisBinding,
    MagisProvisionResult,
    RuntimeEndpoint,
    RuntimeOperationResult,
    RuntimeSpec,
)
from startup.kubernetes.resources import KubernetesEvaBackend


def _verify_request(body: bytes, timestamp: str | None, signature: str | None) -> None:
    secret = get_control_secret()
    if not secret or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="missing control authentication")
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid control timestamp") from exc
    if age > 300:
        raise HTTPException(status_code=401, detail="expired control request")
    expected = hmac.new(
        secret, timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid control signature")


def _k8s_backend() -> KubernetesEvaBackend:
    """Materialise the K8s client — single dependency per plan §6."""
    return KubernetesEvaBackend()


def _to_runtime_result(legacy, spec: RuntimeSpec) -> RuntimeOperationResult:
    endpoint = None
    if legacy.observed_state not in {"stopped", "deleted"} and legacy.deployment_name:
        endpoint = RuntimeEndpoint(
            runtime_id=spec.magi_id,
            backend_kind="kubernetes",
            base_url=f"http://{legacy.deployment_name}:42069",
            backend_ref=legacy.deployment_name,
            observed_state=legacy.observed_state,
        )
    return RuntimeOperationResult(
        runtime_id=spec.magi_id,
        backend_kind="kubernetes",
        backend_ref=legacy.deployment_name,
        observed_state=legacy.observed_state,
        endpoint=endpoint,
        kubernetes_detail=None,
        message=legacy.message,
    )


def _to_runtime_spec(legacy: EvaSpec) -> RuntimeSpec:
    return RuntimeSpec(
        magi_id=legacy.magi_id,
        name=legacy.name,
        magis_id=(legacy.magis.id if legacy.magis is not None else None),
        magis_name=(legacy.magis.name if legacy.magis is not None else None),
    )


def create_app() -> FastAPI:
    app = FastAPI(title="MAGI EVA Orchestrator", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "magi-orchestrator"}

    @app.post("/v1/magis/{magis_id}/provision", response_model=MagisProvisionResult)
    async def provision_magis(
        magis_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> MagisProvisionResult:
        body = await request.body()
        _verify_request(body, x_magi_timestamp, x_magi_signature)
        binding = MagisBinding.model_validate_json(body)
        if binding.id != magis_id:
            raise HTTPException(status_code=400, detail="path/body MAGIS id mismatch")
        k8s = _k8s_backend()
        result = k8s.provision_magis(binding)
        return MagisProvisionResult(
            magis_id=binding.id,
            backend_kind="kubernetes",
            database_service_name=result.database_service_name,
            workspace_claim_name=result.workspace_claim_name,
            message=result.message,
        )

    async def _spec_and_auth(
        request: Request, x_magi_timestamp: str | None, x_magi_signature: str | None
    ) -> EvaSpec:
        body = await request.body()
        _verify_request(body, x_magi_timestamp, x_magi_signature)
        return EvaSpec.model_validate_json(body)

    @app.post("/v1/evas/{magi_id}/start", response_model=RuntimeOperationResult)
    async def start_eva(
        magi_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> RuntimeOperationResult:
        legacy = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if legacy.magi_id != magi_id:
            raise HTTPException(status_code=400, detail="path/body magi id mismatch")
        k8s = _k8s_backend()
        return _to_runtime_result(k8s.start(legacy), _to_runtime_spec(legacy))

    @app.post("/v1/evas/{magi_id}/stop", response_model=RuntimeOperationResult)
    async def stop_eva(
        magi_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> RuntimeOperationResult:
        legacy = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if legacy.magi_id != magi_id:
            raise HTTPException(status_code=400, detail="path/body magi id mismatch")
        k8s = _k8s_backend()
        return _to_runtime_result(k8s.stop(legacy), _to_runtime_spec(legacy))

    @app.post("/v1/evas/{magi_id}/delete", response_model=RuntimeOperationResult)
    async def delete_eva(
        magi_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> RuntimeOperationResult:
        legacy = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if legacy.magi_id != magi_id:
            raise HTTPException(status_code=400, detail="path/body magi id mismatch")
        k8s = _k8s_backend()
        return _to_runtime_result(k8s.delete(legacy), _to_runtime_spec(legacy))

    return app


__all__ = ["create_app", "_verify_request"]
