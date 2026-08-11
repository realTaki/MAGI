"""Fail-fast behaviour when a Runtime is mid-restart.

``magi node run`` serves a Runtime under a Uvicorn reload supervisor
(:func:`magi.startup.runtime._reload_enabled` defaults to on), and the
*supervisor* — not the worker — owns the listening socket.  A worker
restart therefore does not look like "connection refused" to the
control plane; it looks like "connected, then silence", because the
kernel keeps completing handshakes into a backlog nobody is accepting
from.

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
import socket
import threading
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from magi.channels.api import runtime_proxy
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.runtime_http import (
    CONTROL_TIMEOUT,
    LIVENESS_TIMEOUT,
    PROXY_TIMEOUT,
    runtime_is_live,
)


@contextmanager
def _deaf_listener():
    """A bound, listening socket that never calls ``accept()``.

    This is a restarting Uvicorn worker as seen from the client side:
    the reload supervisor still holds the listening socket, so
    ``connect()`` returns immediately and the request then hangs
    waiting for a worker that has not finished booting.
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


async def test_proxy_answers_503_instead_of_stalling_on_a_restart(monkeypatch) -> None:
    """The browser-visible contract: a Runtime cycling under its reload
    supervisor produces an immediate, labelled 503 — not a minute of
    silence that an ingress reports as an unattributable 504."""
    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-control-secret")
    monkeypatch.setattr(runtime_proxy, "get_bus", lambda request: MagicMock())

    from magi.channels.api import auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "selected_session",
        lambda bus, cookie: {"magi_id": 1, "tgid": 42, "admin": True, "assigned": False},
    )

    with _deaf_listener() as base_url:
        monkeypatch.setattr(runtime_proxy, "_runtime_url", lambda bus, magi_id: base_url)
        started = asyncio.get_running_loop().time()
        with pytest.raises(MagiHTTPException) as caught:
            await runtime_proxy.proxy_runtime(1, "contacts", _browser_request())
        elapsed = asyncio.get_running_loop().time() - started

    assert caught.value.status_code == 503
    assert PROXY_TIMEOUT.read is not None and elapsed < PROXY_TIMEOUT.read
