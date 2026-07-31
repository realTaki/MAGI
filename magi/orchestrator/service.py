"""Authenticated FastAPI service that owns EVE Kubernetes operations."""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request

from magi.orchestrator.contracts import EveOperationResult, EveSpec, MagisBinding, MagisProvisionResult
from magi.orchestrator.kubernetes import KubernetesEveBackend


def _verify_request(body: bytes, timestamp: str | None, signature: str | None) -> None:
    secret = os.environ.get("MAGI_CONTROL_SECRET")
    if not secret or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="missing control authentication")
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid control timestamp") from exc
    if age > 300:
        raise HTTPException(status_code=401, detail="expired control request")
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid control signature")


def create_app() -> FastAPI:
    app = FastAPI(title="MAGI EVE Orchestrator", version="0.1.0")

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
        return KubernetesEveBackend().provision_magis(binding)

    async def _spec_and_auth(
        request: Request, x_magi_timestamp: str | None, x_magi_signature: str | None
    ) -> EveSpec:
        body = await request.body()
        _verify_request(body, x_magi_timestamp, x_magi_signature)
        return EveSpec.model_validate_json(body)

    @app.post("/v1/eves/{magic_id}/start", response_model=EveOperationResult)
    async def start_eve(
        magic_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> EveOperationResult:
        spec = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if spec.magic_id != magic_id:
            raise HTTPException(status_code=400, detail="path/body magic id mismatch")
        return KubernetesEveBackend().start(spec)

    @app.post("/v1/eves/{magic_id}/stop", response_model=EveOperationResult)
    async def stop_eve(
        magic_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> EveOperationResult:
        spec = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if spec.magic_id != magic_id:
            raise HTTPException(status_code=400, detail="path/body magic id mismatch")
        return KubernetesEveBackend().stop(spec)

    @app.post("/v1/eves/{magic_id}/delete", response_model=EveOperationResult)
    async def delete_eve(
        magic_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> EveOperationResult:
        spec = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if spec.magic_id != magic_id:
            raise HTTPException(status_code=400, detail="path/body magic id mismatch")
        return KubernetesEveBackend().delete(spec)

    return app
