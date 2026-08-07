# TODO: migrate to new_bus — currently failing under the
# tools/new_bus migration (see magi/startup/runtime.py and
# magi/new_bus). Re-baseline this test file when the agent
# loop moves to bus.tool_job_board + the new ToolWorker.
"""Regression tests for the single-WebUI / per-MAGI Runtime API boundary."""

from __future__ import annotations

from fastapi import Request


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/contacts",
            "query_string": b"page=1",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "scheme": "http",
            "server": ("runtime", 42069),
        }
    )


def test_runtime_proxy_signature_is_bound_to_target_and_path(monkeypatch) -> None:
    from magi.channels.api.proxy_auth import build_proxy_headers, verified_proxy_operator

    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")
    monkeypatch.setenv("MAGI_RUNTIME_ID", "7")
    headers = build_proxy_headers(
        method="GET",
        path_and_query="/api/contacts?page=1",
        target_id=7,
        operator_id=42,
        operator_name="Operator",
        telegram_id=12345,
    )
    assert verified_proxy_operator(_request(headers)) == (42, "Operator", 12345)

    headers["X-MAGI-Proxy-Target"] = "8"
    assert verified_proxy_operator(_request(headers)) is None


def test_runtime_app_has_no_spa_or_browser_login_routes() -> None:
    from magi.channels.api.app import create_runtime_app

    app = create_runtime_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    # MAGIS management is target-scoped and therefore lives in the selected
    # runtime; browser login and the SPA remain control-plane only.
    assert "/api/auth/available-magi" not in paths
    assert "/" not in paths
