"""Authentication shared by the WebUI control plane and MAGI Runtime APIs.

The browser authenticates only to the single WebUI service.  When that service
needs private state from a selected MAGI, it signs a short-lived request with
``MAGI_CONTROL_SECRET``.  Runtime APIs accept that request only when its target
matches their own ``MAGI_RUNTIME_ID``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Mapping

from fastapi import Request

_MAX_AGE_SECONDS = 60


def _secret() -> bytes | None:
    value = os.environ.get("MAGI_CONTROL_SECRET")
    return value.encode() if value else None


def _canonical(method: str, path_and_query: str, timestamp: str, target_id: str, operator_id: str, scope: str = "") -> bytes:
    return "\n".join((method.upper(), path_and_query, timestamp, target_id, operator_id, scope)).encode()


def build_proxy_headers(*, method: str, path_and_query: str, target_id: int, operator_id: int, operator_name: str, telegram_id: int | None, admin: bool = True, assigned: bool = False) -> dict[str, str]:
    """Return service-to-service headers for one selected MAGI request."""
    secret = _secret()
    if secret is None:
        raise RuntimeError("MAGI_CONTROL_SECRET is required for WebUI runtime proxying")
    timestamp = str(int(time.time()))
    target = str(target_id)
    operator = str(operator_id)
    scope = f"admin={int(admin)};assigned={int(assigned)}"
    signature = hmac.new(
        secret,
        _canonical(method, path_and_query, timestamp, target, operator, scope),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-MAGI-Proxy-Timestamp": timestamp,
        "X-MAGI-Proxy-Target": target,
        "X-MAGI-Proxy-Operator": operator,
        "X-MAGI-Proxy-Operator-Name": operator_name[:120],
        "X-MAGI-Proxy-Scope": scope,
        "X-MAGI-Proxy-Signature": signature,
    }
    if telegram_id is not None:
        headers["X-MAGI-Proxy-Telegram-ID"] = str(telegram_id)
    return headers


def verified_proxy_operator(request: Request) -> tuple[int, str, int | None] | None:
    """Validate a WebUI-to-runtime request and return its operator identity."""
    secret = _secret()
    timestamp = request.headers.get("X-MAGI-Proxy-Timestamp", "")
    target = request.headers.get("X-MAGI-Proxy-Target", "")
    operator = request.headers.get("X-MAGI-Proxy-Operator", "")
    signature = request.headers.get("X-MAGI-Proxy-Signature", "")
    if secret is None or not all((timestamp, target, operator, signature)):
        return None
    try:
        if abs(time.time() - int(timestamp)) > _MAX_AGE_SECONDS:
            return None
        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if not runtime_id or int(target) != int(runtime_id):
            return None
        operator_id = int(operator)
    except ValueError:
        return None
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    scope = request.headers.get("X-MAGI-Proxy-Scope", "")
    if scope not in {"admin=0;assigned=0", "admin=0;assigned=1", "admin=1;assigned=0", "admin=1;assigned=1"}:
        return None
    expected = hmac.new(
        secret,
        _canonical(request.method, path, timestamp, target, operator, scope),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    telegram_raw = request.headers.get("X-MAGI-Proxy-Telegram-ID")
    try:
        telegram_id = int(telegram_raw) if telegram_raw else None
    except ValueError:
        return None
    return operator_id, request.headers.get("X-MAGI-Proxy-Operator-Name", "WebUI operator"), telegram_id


def verified_proxy_scope(request: Request) -> tuple[bool, bool] | None:
    """Return the signed MAGIS-admin / local-assigned capabilities."""
    if verified_proxy_operator(request) is None:
        return None
    parts = dict(item.split("=", 1) for item in request.headers["X-MAGI-Proxy-Scope"].split(";"))
    return parts["admin"] == "1", parts["assigned"] == "1"


def ensure_runtime_operator(request: Request) -> int | None:
    """Materialise the authenticated control operator in this MAGI's SQLite.

    Contacts are private MAGI data, so their numeric IDs are not global.  A
    verified control request is mapped by Telegram identity when available; an
    explicit system marker covers WebUI-only operators.  The returned local
    contact ID keeps existing chat/session APIs correctly scoped.
    """
    identity = verified_proxy_operator(request)
    if identity is None:
        return None
    operator_id, name, telegram_id = identity
    scope = verified_proxy_scope(request)
    if scope is None:
        return None
    is_admin, is_assigned = scope
    import os

    from magi.channels.api._bus import bus

    return bus.contacts.ensure_runtime_operator(
        operator_id=operator_id,
        name=name,
        telegram_id=telegram_id,
        is_admin=is_admin,
        is_assigned=is_assigned,
    )


__all__ = ["build_proxy_headers", "ensure_runtime_operator", "verified_proxy_operator", "verified_proxy_scope"]
