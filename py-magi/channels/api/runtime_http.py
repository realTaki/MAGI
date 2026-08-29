"""Timeout policy for control-plane → MAGI Runtime HTTP calls.

Every call the WebUI control plane makes into a Runtime crosses a process
boundary that can stop or become unresponsive mid-request.  A TCP listener
can accept a connection but fail to answer — for example while a process is
wedged or an upstream network path is unhealthy.

A flat ``timeout=30.0`` turns that window into a 30-second hang.  Behind
a tunnel or ingress whose own gateway timeout is shorter, the browser
never sees our error at all — it gets an opaque 504 instead of the
actionable 503 the handler would have raised.  Splitting the budget per
phase is what makes the failure legible:

``connect`` / ``pool``
    Deliberately short.  A Runtime that is genuinely down refuses the
    connection at once, and there is no reason to wait longer than a
    couple of seconds for a socket on localhost or a cluster-internal
    Service.

``read``
    Where the two call sites diverge.  Control-plane calls (login
    target lookup and Telegram bootstrap) are small
    local writes that finish in milliseconds, so :data:`CONTROL_TIMEOUT`
    caps them tightly.  The generic proxy in
    :mod:`channels.api.runtime_proxy` forwards *arbitrary* Runtime
    endpoints, some legitimately slow — ``GET
    /api/mcp-servers/{name}/tools`` dials a third-party MCP server under
    its own 60-second ``execute_timeout`` — so :data:`PROXY_TIMEOUT`
    keeps a generous read budget and leans on :func:`runtime_is_live`
    to tell "restarting" apart from "working".
"""

from __future__ import annotations

import httpx

# Login and bootstrap calls. Each one is a small write
# against the target Runtime's local SQLite, so ten seconds is already
# an order of magnitude more than the slowest healthy case; anything
# beyond that means the far side is restarting, not thinking.
CONTROL_TIMEOUT = httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=2.0)

# Control-plane legs whose far side wraps a *third-party* call. These need a
# read budget strictly larger than the inner one, or the outer timeout fires
# while the inner request is still legitimately in flight and we report a
# failure for work that is about to succeed.
#
# The inner budget here is Telegram: every helper in
# :mod:`channels.telegram.bot` calls ``urllib.request.urlopen(...,
# timeout=10)``. That ten seconds is a *socket* timeout — it applies per
# operation (connect, then each read), not to the request as a whole — so a
# slow-but-alive api.telegram.org can legitimately keep the runtime busy for
# appreciably longer than ten seconds before its own timeout fires. Thirty
# seconds restores the headroom the flat ``timeout=30.0`` used to provide,
# while keeping the short ``connect`` that makes a genuinely dead runtime
# fail fast.
RELAY_TIMEOUT = httpx.Timeout(connect=2.0, read=30.0, write=10.0, pool=2.0)


# The generic browser-facing proxy. ``read`` stays at the historical 60
# seconds because the path set includes endpoints that legitimately
# block on third parties (MCP tool discovery). Fast failure for the
# restart case comes from :func:`runtime_is_live`, not from this
# budget.
PROXY_TIMEOUT = httpx.Timeout(connect=2.0, read=60.0, write=10.0, pool=2.0)

# The liveness probe itself. ``/health`` touches no I/O, so a healthy
# Runtime answers in single-digit milliseconds either way; two seconds bound
# the window before an unavailable runtime is reported to the caller.
LIVENESS_TIMEOUT = httpx.Timeout(2.0)


async def runtime_is_live(base_url: str) -> bool:
    """Report whether ``base_url`` is accepting requests *right now*.

    Used as a pre-flight before forwarding a request whose own read
    budget is too generous to double as a restart detector.  ``/health``
    is unauthenticated and does no I/O, which is what makes it usable
    here: when the process or its path is unresponsive it hangs just like the
    real request would, so a two-second timeout converts a 60-second stall
    into an immediate 503.

    This narrows the failure window rather than closing it — the Runtime can
    still become unavailable in the milliseconds between the probe and the
    forwarded request.

    The client is a module-level singleton so connection setup (TCP
    handshake on localhost is sub-millisecond, but the SSL context and
    HTTP/1.1 keep-alive state are several hundred milliseconds to spin up
    cold) and HTTP keep-alive are reused across calls.  Measured cost on
    a healthy localhost target is ~65 ms per call with a fresh client,
    ~2 ms with a warm one; a dashboard page triggers ~10 such probes,
    so the difference is ~600 ms per page.  A single client is enough
    because the WebUI control plane talks to at most one Runtime per
    request, all over the same process; the real fan-out (multiple
    distinct runtimes) only happens in tests.
    """
    client = _probe_client()
    try:
        response = await client.get(f"{base_url.rstrip('/')}/health")
    except httpx.HTTPError:
        return False
    # A booting worker can bind and answer before its dependencies are
    # ready; treat any 5xx as "not live yet" so the caller reports the
    # same 503 it would for a refused connection.
    return not response.is_server_error


def _probe_client() -> httpx.AsyncClient:
    """Lazy module-level probe client.

    Lazily constructed so tests that import this module without ever
    calling ``runtime_is_live`` do not open an HTTP client they cannot
    close.  The singleton lifetime is the process lifetime.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(timeout=LIVENESS_TIMEOUT)
    return _CLIENT


_CLIENT: httpx.AsyncClient | None = None


__all__ = [
    "CONTROL_TIMEOUT",
    "LIVENESS_TIMEOUT",
    "PROXY_TIMEOUT",
    "RELAY_TIMEOUT",
    "runtime_is_live",
]
