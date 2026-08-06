"""Authentication and contracts for the restricted control plane.

Tests the FastAPI orchestrator service that lives in
:meth:`magi.startup.kubernetes.service.create_app` per plan §20.4.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException


def test_orchestrator_rejects_missing_hmac(monkeypatch):
    from magi.startup.kubernetes.service import _verify_request

    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")
    with pytest.raises(HTTPException, match="missing control authentication"):
        _verify_request(b"{}", None, None)


def test_orchestrator_accepts_valid_hmac(monkeypatch):
    from magi.startup.kubernetes.service import _verify_request

    secret, body, timestamp = "test-control-secret", b'{"magic_id":7}', str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    monkeypatch.setenv("MAGI_CONTROL_SECRET", secret)
    _verify_request(body, timestamp, signature)


def test_control_plane_exposes_magis_provision_route():
    from magi.startup.kubernetes.service import create_app

    paths = {route.path for route in create_app().routes}
    assert "/v1/magis/{magis_id}/provision" in paths
    assert "/v1/evas/{magic_id}/start" in paths