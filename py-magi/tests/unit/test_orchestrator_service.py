"""Authentication and contracts for the restricted control plane.

Tests the FastAPI orchestrator service that lives in
:meth:`magi.startup.kubernetes.service.create_app` per plan §20.4.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from types import MethodType

import pytest
from fastapi import HTTPException


def test_orchestrator_rejects_missing_hmac(monkeypatch):
    from magi.startup.kubernetes.service import _verify_request

    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")
    with pytest.raises(HTTPException, match="missing control authentication"):
        _verify_request(b"{}", None, None)


def test_orchestrator_accepts_valid_hmac(monkeypatch):
    from magi.startup.kubernetes.service import _verify_request

    secret, body, timestamp = "test-control-secret", b'{"magi_id":7}', str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    monkeypatch.setenv("MAGI_CONTROL_SECRET", secret)
    _verify_request(body, timestamp, signature)


def test_control_plane_exposes_magis_provision_route():
    from magi.startup.kubernetes.service import create_app

    paths = {route.path for route in create_app().routes}
    assert "/v1/magis/{magis_id}/provision" in paths
    assert "/v1/evas/{magi_id}/start" in paths


def test_kubernetes_magis_share_one_postgres_service_but_use_distinct_databases(monkeypatch):
    from magi.startup.kubernetes.contracts import MagisBinding
    from magi.startup.kubernetes.resources import KubernetesEvaBackend

    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")
    backend = object.__new__(KubernetesEvaBackend)
    backend.namespace = "test"
    applied: list[tuple[str, dict]] = []
    backend._apply = MethodType(
        lambda _self, path, manifest: applied.append((path, manifest)), backend
    )

    first = backend.provision_magis(MagisBinding(id=42, name="Research"))
    first_paths = [path for path, _ in applied]
    second = backend.provision_magis(MagisBinding(id=43, name="Operations"))
    second_paths = [path for path, _ in applied[len(first_paths) :]]

    assert first.database_service_name == second.database_service_name == "magi-magis-postgres"
    assert any(path.endswith("/deployments/magi-magis-postgres") for path in first_paths)
    assert any(path.endswith("/secrets/magi-magis-42-research-db") for path in first_paths)
    assert any(path.endswith("/secrets/magi-magis-43-operations-db") for path in second_paths)
    first_job = next(
        manifest
        for path, manifest in applied
        if path.endswith("/jobs/magi-magis-42-research-db-init")
    )
    assert (
        "CREATE DATABASE magis_42"
        in first_job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    )
