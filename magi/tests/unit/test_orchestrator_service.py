"""The controller accepts only signed, fixed-shape lifecycle requests."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi.testclient import TestClient


def _signed_headers(secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "content-type": "application/json",
        "X-MAGI-Timestamp": timestamp,
        "X-MAGI-Signature": signature,
    }


def test_orchestrator_requires_valid_hmac(monkeypatch):
    from magi.orchestrator.service import create_app

    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")
    client = TestClient(create_app())
    response = client.post("/v1/eves/7/start", content=b"{}")
    assert response.status_code == 401


def test_orchestrator_dispatches_signed_start(monkeypatch):
    import magi.orchestrator.service as service
    from magi.orchestrator.contracts import EveOperationResult

    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")

    class FakeBackend:
        def start(self, spec):
            assert spec.magi_id == 7
            return EveOperationResult(
                observed_state="provisioning", namespace="magi", deployment_name="magi-eve-7-worker",
                workspace_claim_name="magi-eve-7-worker-workspace",
                credential_secret_name="magi-eve-7-worker-provider",
            )

    monkeypatch.setattr(service, "KubernetesEveBackend", FakeBackend)
    client = TestClient(service.create_app())
    body = b'{"magi_id":7,"magis_id":1,"name":"worker","provider":"claude","api_key":"secret"}'
    response = client.post("/v1/eves/7/start", content=body, headers=_signed_headers("test-control-secret", body))
    assert response.status_code == 200, response.text
    assert response.json()["observed_state"] == "provisioning"
