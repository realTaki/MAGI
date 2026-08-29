"""Regression tests for selected-MAGI admin sessions and runtime proxying."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Request

from channels.api.app import create_runtime_app
from startup.workers import WorkerRegistry


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": "/api/contacts",
            "query_string": b"page=1",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "scheme": "http", "server": ("runtime", 42069),
        }
    )


def test_runtime_proxy_signature_is_bound_to_admin_identity_target_and_path(monkeypatch) -> None:
    from channels.api.proxy_auth import build_proxy_headers, verified_proxy_operator, verified_proxy_scope

    monkeypatch.setenv("MAGI_RUNTIME_ID", "7")
    bus = MagicMock()
    bus.magis_name = "test"
    bus.control_secrets_book.get_by_name.return_value = MagicMock(
        secret_value=b"test-control-secret",
    )
    headers = build_proxy_headers(
        bus=bus,
        method="GET", path_and_query="/api/contacts?page=1", target_id=7,
        operator_id=42, operator_name="Operator", tgid=12345,
        magis_admin_id=11, admin=True, two_factor=True,
    )
    request = _request(headers)
    assert verified_proxy_operator(bus, request) == (42, "Operator", 12345)
    assert verified_proxy_scope(bus, request) == (True, False, True, 11)

    headers["X-MAGI-Proxy-Target"] = "8"
    assert verified_proxy_operator(bus, _request(headers)) is None


def test_selected_session_keeps_shared_admin_and_local_projection_distinct() -> None:
    from channels.api.auth import _sign_selected_session, resolve_session

    bus = MagicMock()
    bus.settings_book.get_value.return_value = "test-signing-secret"
    token = _sign_selected_session(
        bus, magi_id=7, contact_id=3, magis_admin_id=11, tgid=987654321,
        display_name="Operator", admin=True, assigned=False, two_factor=True,
    )
    session = resolve_session(bus, token)
    assert session is not None
    assert session["contact_id"] == 3
    assert session["magis_admin_id"] == 11
    assert session["tgid"] == 987654321
    assert session["two_factor"] is True


def test_runtime_app_has_no_spa_or_browser_login_routes() -> None:
    app = create_runtime_app(bus=MagicMock(), workers=MagicMock(spec=WorkerRegistry))
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/auth/available-magi" not in paths
    assert "/" not in paths
