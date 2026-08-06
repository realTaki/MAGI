"""Authenticated FastAPI service that owns EVA Kubernetes operations.

Per plan §6 — there is no Backend abstraction. The orchestrator uses
the Kubernetes path directly via :class:`magi.orchestrator.kubernetes.KubernetesEvaBackend`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request

from magi.bus.jobs.protocols.lifecycle import (
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)
from magi.orchestrator.contracts import EvaSpec, MagisBinding
from magi.orchestrator.kubernetes import KubernetesEvaBackend


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
        # Phase 2 — routed through the backend factory so the
        # ``MAGI_BACKEND`` env var selects the implementation.
        backend = create_backend()
        return backend.provision_magis(magis_id=binding.id, magis_name=binding.name)

    async def _spec_and_auth(
        request: Request, x_magi_timestamp: str | None, x_magi_signature: str | None
    ) -> EvaSpec:
        body = await request.body()
        _verify_request(body, x_magi_timestamp, x_magi_signature)
        return EvaSpec.model_validate_json(body)

    def _to_runtime_spec(legacy: EvaSpec) -> RuntimeSpec:
        return RuntimeSpec(
            magic_id=legacy.magic_id,
            name=legacy.name,
            magis_id=(legacy.magis.id if legacy.magis is not None else None),
            magis_name=(legacy.magis.name if legacy.magis is not None else None),
        )

    @app.post("/v1/evas/{magic_id}/start", response_model=RuntimeOperationResult)
    async def start_eva(
        magic_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> RuntimeOperationResult:
        legacy = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if legacy.magic_id != magic_id:
            raise HTTPException(status_code=400, detail="path/body magic id mismatch")
        backend = create_backend()
        return backend.start(_to_runtime_spec(legacy))

    @app.post("/v1/evas/{magic_id}/stop", response_model=RuntimeOperationResult)
    async def stop_eva(
        magic_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> RuntimeOperationResult:
        legacy = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if legacy.magic_id != magic_id:
            raise HTTPException(status_code=400, detail="path/body magic id mismatch")
        backend = create_backend()
        return backend.stop(_to_runtime_spec(legacy))

    @app.post("/v1/evas/{magic_id}/delete", response_model=RuntimeOperationResult)
    async def delete_eva(
        magic_id: int,
        request: Request,
        x_magi_timestamp: str | None = Header(default=None),
        x_magi_signature: str | None = Header(default=None),
    ) -> RuntimeOperationResult:
        legacy = await _spec_and_auth(request, x_magi_timestamp, x_magi_signature)
        if legacy.magic_id != magic_id:
            raise HTTPException(status_code=400, detail="path/body magic id mismatch")
        backend = create_backend()
        return backend.delete(_to_runtime_spec(legacy))

    return app
