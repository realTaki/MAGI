"""Fail-fast behaviour when a Runtime is unresponsive.

An unavailable Runtime does not always look like "connection refused" to the
control plane.  It can look like "connected, then silence" when a listener
or network path accepts the handshake but never returns an HTTP response.

That distinction is the whole point of this module.  A flat
``timeout=30.0`` is invisible in the healthy case and turns every
restart into a 30-second stall — which a tunnel or ingress converts
into an opaque 504 long before the handler's own 503 would reach the
browser.  ``_deaf_listener`` reproduces the restart window exactly:
it binds and listens but never accepts, so a client connects
successfully and then waits forever.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import socket
import threading
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from channels.api import runtime_proxy
from channels.api.errors import MagiHTTPException
from channels.api.runtime_http import (
    CONTROL_TIMEOUT,
    LIVENESS_TIMEOUT,
    PROXY_TIMEOUT,
    RELAY_TIMEOUT,
    runtime_is_live,
)


@contextmanager
def _deaf_listener():
    """A bound, listening socket that never calls ``accept()``.

    This models an unresponsive Runtime as seen from the client side:
    ``connect()`` returns immediately and the request then hangs forever.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        sock.close()


@contextmanager
def _health_server(status: int):
    """A real HTTP server answering ``/health`` with ``status``."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib naming
            self.send_response(status)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass  # keep pytest output clean

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- timeout budgets ---------------------------------------------------


def test_connect_and_pool_budgets_are_short_on_every_runtime_call() -> None:
    """A socket to localhost or a cluster Service either answers at once
    or is not there. Waiting tens of seconds for it only delays the 503."""
    for budget in (CONTROL_TIMEOUT, PROXY_TIMEOUT):
        assert budget.connect is not None and budget.connect <= 2.0
        assert budget.pool is not None and budget.pool <= 2.0


def test_control_plane_reads_are_capped_far_below_a_gateway_timeout() -> None:
    """Login / onboarding / bootstrap calls are small local writes on the
    far side. Ten seconds is already orders of magnitude more than the
    slowest healthy case, and well under any ingress' 504 threshold."""
    assert CONTROL_TIMEOUT.read is not None and CONTROL_TIMEOUT.read <= 10.0


def test_the_generic_proxy_keeps_a_read_budget_for_third_party_calls() -> None:
    """The proxy forwards arbitrary Runtime endpoints, and some block on
    third parties — ``GET /api/mcp-servers/{name}/tools`` dials an MCP
    server under its own 60-second ``execute_timeout``. Shortening this
    would trade a rare stall for routine false failures, which is why
    restart detection lives in ``runtime_is_live`` instead."""
    assert PROXY_TIMEOUT.read is not None and PROXY_TIMEOUT.read >= 60.0


# -- nested budgets ----------------------------------------------------
#
# A control-plane leg whose far side calls a third party must outlast that
# third party's own budget. Get this backwards and the outer timeout fires
# while the inner request is still in flight, so the operator is told the
# work failed at the exact moment it was about to succeed. These tests read
# the inner budgets out of the real source rather than restating them, so
# raising Telegram's socket timeout without raising RELAY_TIMEOUT fails
# here instead of in production.


def _telegram_socket_timeout() -> float:
    """The ``urlopen(..., timeout=N)`` budget in the Telegram helpers."""
    source = pathlib.Path("magi/channels/telegram/bot.py").read_text(encoding="utf-8")
    timeouts = {float(m) for m in re.findall(r"urlopen\([^)]*timeout=(\d+(?:\.\d+)?)", source)}
    assert timeouts, "expected urlopen(..., timeout=N) in the Telegram helpers"
    return max(timeouts)


def test_relay_budget_outlasts_telegrams_own_timeout() -> None:
    """``RELAY_TIMEOUT`` wraps runtime handlers that end in
    ``tg_bot.send_text_raw``. Telegram's ten seconds is a *socket* timeout —
    per operation, not per request — so the outer budget needs real headroom
    over it, not parity."""
    inner = _telegram_socket_timeout()
    assert RELAY_TIMEOUT.read is not None
    assert RELAY_TIMEOUT.read >= inner * 2, (
        f"RELAY_TIMEOUT.read={RELAY_TIMEOUT.read} leaves no headroom over "
        f"Telegram's {inner}s socket timeout"
    )


def test_the_tight_control_budget_would_not_survive_a_telegram_hop() -> None:
    """Pins *why* the relay budget exists as a separate constant: the
    control budget is deliberately too small to wrap a Telegram call, so any
    future call site that routes one through ``CONTROL_TIMEOUT`` is a bug."""
    assert CONTROL_TIMEOUT.read is not None
    assert CONTROL_TIMEOUT.read <= _telegram_socket_timeout()


def test_telegram_backed_call_sites_use_the_relay_budget() -> None:
    """The two legs that reach api.telegram.org must not be on the tight
    control budget. Checked against the source so a future edit that swaps
    the constant back is caught here."""
    control_runtime = pathlib.Path("magi/channels/api/control_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "AsyncClient(timeout=RELAY_TIMEOUT)" in control_runtime
    assert "CONTROL_TIMEOUT" not in control_runtime

    auth = pathlib.Path("magi/channels/api/auth.py").read_text(encoding="utf-8")
    send_login_code = auth[auth.index("async def target_send_login_code") :][:900]
    assert "timeout=RELAY_TIMEOUT" in send_login_code


# -- liveness probe ----------------------------------------------------


async def test_a_restarting_runtime_is_not_live_and_is_detected_quickly() -> None:
    with _deaf_listener() as base_url:
        started = asyncio.get_running_loop().time()
        assert await runtime_is_live(base_url) is False
        elapsed = asyncio.get_running_loop().time() - started

    # The probe must be bounded by its own timeout, not by the caller's
    # far more generous read budget — that gap is the entire fix.
    assert elapsed < LIVENESS_TIMEOUT.read + 3.0
    assert PROXY_TIMEOUT.read is not None and elapsed < PROXY_TIMEOUT.read


async def test_a_healthy_runtime_is_live() -> None:
    with _health_server(200) as base_url:
        assert await runtime_is_live(base_url) is True


async def test_a_booting_worker_answering_5xx_is_not_live_yet() -> None:
    """A worker can bind and serve before its dependencies are ready.
    Forwarding into that window produces a confusing upstream error;
    reporting it as "not live" keeps the browser's 503 consistent."""
    with _health_server(503) as base_url:
        assert await runtime_is_live(base_url) is False


async def test_a_closed_port_is_not_live() -> None:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    assert await runtime_is_live(f"http://127.0.0.1:{dead_port}") is False


async def test_runtime_is_live_uses_a_shared_http_client() -> None:
    """Pin the perf invariant: the probe must reuse one ``AsyncClient``
    across calls.  Without reuse every proxied request pays ~60 ms of
    connection setup on top of the ~1 ms TCP round trip — a typical
    dashboard hits this path ten times per page, so the regression is
    ~600 ms per page that no caller would notice locally.  We assert
    on the singleton rather than the wall clock so the test stays fast
    and deterministic across machines."""
    from channels.api import runtime_http

    # The probe was just used by the earlier tests, so the singleton
    # has been created.  All later calls must reach for the SAME object.
    first = runtime_http._probe_client()
    second = runtime_http._probe_client()
    assert first is second, "probe client should be a singleton"

    # Drive runtime_is_live through its public path and confirm it
    # actually reaches _probe_client() rather than allocating its own.
    # Capture the original first so the spy doesn't recurse into itself.
    seen: list[int] = []
    original = runtime_http._probe_client

    def _spy():
        c = original()
        seen.append(id(c))
        return c

    runtime_http._probe_client = _spy  # type: ignore[assignment]
    try:
        with _health_server(200) as base_url:
            assert await runtime_http.runtime_is_live(base_url) is True
    finally:
        runtime_http._probe_client = original  # type: ignore[assignment]

    assert seen, "runtime_is_live did not call _probe_client"


# -- proxy integration -------------------------------------------------


def _browser_request() -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/contacts",
            "query_string": b"",
            "headers": [(b"cookie", b"magi_session=v4.stub")],
            "scheme": "http",
            "server": ("webui", 42069),
        },
        receive,
    )


async def test_proxy_answers_503_instead_of_stalling_on_an_unresponsive_runtime(monkeypatch) -> None:
    """The browser-visible contract is an immediate, labelled 503 — not a
    minute of silence that an ingress reports as an unattributable 504."""
    proxy_bus = MagicMock()
    proxy_bus.magis_name = "test"
    proxy_bus.control_secrets_book.get_by_name.return_value = MagicMock(
        secret_value=b"test-control-secret",
    )
    monkeypatch.setattr(runtime_proxy, "get_bus", lambda _request: proxy_bus)

    from channels.api import auth as auth_mod

    # Match the v5 session payload shape ``_sign_selected_session`` mints —
    # the proxy reaches into ``contact_id``, ``tgid``, ``admin``, ``assigned``
    # via ``browser_session[...]``.
    monkeypatch.setattr(
        auth_mod,
        "selected_session",
        lambda _bus, _cookie: {
            "magi_id": 1,
            "contact_id": 42,
            "magis_admin_id": 7,
            "tgid": 42,
            "display_name": "Tester",
            "admin": True,
            "assigned": False,
            "two_factor": False,
        },
    )

    with _deaf_listener() as base_url:
        monkeypatch.setattr(runtime_proxy, "_runtime_url", lambda _bus, _magi_id: base_url)
        started = asyncio.get_running_loop().time()
        with pytest.raises(MagiHTTPException) as caught:
            await runtime_proxy.proxy_runtime(1, "contacts", _browser_request())
        elapsed = asyncio.get_running_loop().time() - started

    assert caught.value.status_code == 503
    assert PROXY_TIMEOUT.read is not None and elapsed < PROXY_TIMEOUT.read
