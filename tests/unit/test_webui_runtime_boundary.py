# TODO: migrate to bus — currently failing under the
# tools/bus migration (see magi/startup/runtime.py and
# magi/bus). Re-baseline this test file when the agent
# loop moves to bus.tool_job_board + the new ToolWorker.
"""Regression tests for the single-WebUI / per-MAGI Runtime API boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import Request

from magi.channels.api.app import create_runtime_app
from magi.startup.workers import WorkerRegistry


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
        tgid=12345,
    )
    assert verified_proxy_operator(_request(headers)) == (42, "Operator", 12345)

    headers["X-MAGI-Proxy-Target"] = "8"
    assert verified_proxy_operator(_request(headers)) is None


def test_runtime_app_has_no_spa_or_browser_login_routes() -> None:
    app = create_runtime_app(
        context=SimpleNamespace(
            bus=MagicMock(),
            workers=MagicMock(spec=WorkerRegistry),
        )
    )
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    # MAGIS management is target-scoped and therefore lives in the selected
    # runtime; browser login and the SPA remain control-plane only.
    assert "/api/auth/available-magi" not in paths
    assert "/" not in paths


def test_apps_keep_explicit_bus_instances_isolated() -> None:
    from magi.channels.api.dependencies import get_bus

    first, second = MagicMock(), MagicMock()
    first_app = create_runtime_app(
        context=SimpleNamespace(
            bus=first,
            workers=MagicMock(spec=WorkerRegistry),
        )
    )
    second_app = create_runtime_app(
        context=SimpleNamespace(
            bus=second,
            workers=MagicMock(spec=WorkerRegistry),
        )
    )

    assert get_bus(Request({"type": "http", "app": first_app})) is first
    assert get_bus(Request({"type": "http", "app": second_app})) is second


def test_selected_session_keeps_contact_id_and_tgid_distinct() -> None:
    """A TG-bound operator's contact_id must survive the cookie round-trip.

    The v4 payload carries ``contact_id`` and ``tgid`` in
    separate slots. An earlier draft stored a single slot
    that held the tgid when the contact had a TG binding
    and fell back to the contact_id when they did not;
    :func:`resolve_session` then read that one slot as the
    contact_id, so every TG-bound operator was handed their
    Telegram chat id as their identity. The two values are
    deliberately far apart here so a regression cannot pass
    by coincidence.
    """
    from magi.channels.api.auth import _sign_selected_session, resolve_session

    bus = MagicMock()
    bus.settings_book.get.return_value = "test-signing-secret"

    token = _sign_selected_session(
        bus,
        magi_id=7,
        contact_id=3,
        tgid=987654321,
        display_name="Operator",
        admin=True,
        assigned=False,
    )
    session = resolve_session(bus, token)

    assert session is not None
    assert session["contact_id"] == 3
    assert session["tgid"] == 987654321
    assert session["magi_id"] == 7


def test_selected_session_allows_a_webui_only_operator_without_tgid() -> None:
    """``tgid=None`` is legitimate — a WebUI-only operator has no TG binding.

    Guards the other direction of the same fix: making
    ``tgid`` nullable must not make the cookie unreadable,
    otherwise password-only operators cannot stay signed in.
    """
    from magi.channels.api.auth import _sign_selected_session, resolve_session

    bus = MagicMock()
    bus.settings_book.get.return_value = "test-signing-secret"

    token = _sign_selected_session(
        bus,
        magi_id=7,
        contact_id=3,
        tgid=None,
        display_name="WebUI operator",
        admin=False,
        assigned=True,
    )
    session = resolve_session(bus, token)

    assert session is not None
    assert session["contact_id"] == 3
    assert session["tgid"] is None
