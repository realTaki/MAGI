"""Authentication shared by the WebUI control plane and MAGI Runtime APIs.

The browser authenticates only to the single WebUI service.  When that service
needs private state from a selected MAGI, it signs a short-lived request with
the HMAC key derived from the per-MAGIS ``control_secrets`` row (provisions
during ``magi init``).  Runtime APIs accept that request only when its target
matches their own ``MAGI_RUNTIME_ID``.

The secret is read exclusively from the DB-backed ``bus.control_secrets_book``
row.  No environment variable is consulted at runtime.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import Request

_MAX_AGE_SECONDS = 60


def _control_secret_name(bus) -> str:
    """Resolve the MAGIS-name under which ``control_secrets`` is keyed.

    Single source of truth for both ``_signing_key`` (session cookies)
    and ``resolve_control_secret`` (proxy HMAC).  Prefers
    ``bus.magis_name``; falls back to the ``MAGIS_NAME`` startup-contract
    env var (set by the launcher in both webui and node child processes).
    As a last resort uses the literal ``"default"`` so a misconfigured
    runtime doesn't crash the proxy HMAC path — it just fails the lookup
    one level up, which is the desired fail-closed behaviour.
    """
    import os

    name = getattr(bus, "magis_name", None)
    if isinstance(name, str) and name:
        return name
    env_name = os.environ.get("MAGIS_NAME")
    if env_name:
        return env_name
    return "default"


def resolve_control_secret(bus) -> bytes | None:
    """Return the raw ``control_secrets`` row value for the current MAGIS.

    Returns ``None`` when:
      - the Bus is unbound to a MAGIS store, or
      - the ``control_secrets_book`` has not been wired, or
      - no row exists for this MAGIS, or
      - the row's ``secret_value`` is empty.

    Callers fail closed on ``None`` (proxy HMAC verification returns
    ``None``; ``build_proxy_headers`` raises ``RuntimeError``).
    """
    if bus is None:
        return None
    book = getattr(bus, "control_secrets_book", None)
    if book is None:
        return None
    try:
        name = _control_secret_name(bus)
    except RuntimeError:
        return None
    row = book.get_by_name(name=name)
    if row is None or row.secret_value is None:
        return None
    return row.secret_value


def _canonical(
    method: str,
    path_and_query: str,
    timestamp: str,
    target_id: str,
    operator_id: str,
    scope: str = "",
) -> bytes:
    return "\n".join(
        (method.upper(), path_and_query, timestamp, target_id, operator_id, scope)
    ).encode()


def build_proxy_headers(
    *,
    bus,
    method: str,
    path_and_query: str,
    target_id: int,
    operator_id: int,
    operator_name: str,
    tgid: int | None,
    magis_admin_id: int | None = None,
    admin: bool = False,
    assigned: bool = False,
    two_factor: bool = False,
) -> dict[str, str]:
    """Return service-to-service headers for one selected MAGI request."""
    secret = resolve_control_secret(bus)
    if secret is None:
        raise RuntimeError(
            "control_secrets row is unavailable; cannot sign proxy "
            "request. Run `magi init` to provision the control secret."
        )
    timestamp = str(int(time.time()))
    target = str(target_id)
    operator = str(operator_id)
    if admin != (magis_admin_id is not None):
        raise ValueError("admin proxy calls must carry exactly one magis_admin_id")
    scope = (
        f"admin={int(admin)};assigned={int(assigned)};"
        f"two_factor={int(two_factor)};magis_admin_id={magis_admin_id or 0}"
    )
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
    if tgid is not None:
        headers["X-MAGI-Proxy-Tgid"] = str(tgid)
    return headers


def verified_proxy_operator(
    bus, request: Request
) -> tuple[int, str, int | None] | None:
    """Validate a WebUI-to-runtime request and return its operator identity."""
    secret = resolve_control_secret(bus)
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
    try:
        parts = dict(item.split("=", 1) for item in scope.split(";"))
        is_admin = parts["admin"] == "1"
        is_assigned = parts["assigned"] == "1"
        magis_admin_id = int(parts["magis_admin_id"])
        two_factor = parts["two_factor"] == "1"
    except (KeyError, ValueError):
        return None
    if (
        parts["admin"] not in {"0", "1"}
        or parts["assigned"] not in {"0", "1"}
        or parts["two_factor"] not in {"0", "1"}
    ):
        return None
    if is_admin != (magis_admin_id > 0):
        return None
    expected = hmac.new(
        secret,
        _canonical(request.method, path, timestamp, target, operator, scope),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    # Accept the pre-rename spelling too: the header is optional and is
    # not covered by the HMAC, so during a rolling update a control-plane
    # pod still on the old build would otherwise silently drop the tgid
    # rather than fail loudly. Safe to delete once every pod is past this
    # release.
    telegram_raw = request.headers.get("X-MAGI-Proxy-Tgid") or request.headers.get(
        "X-MAGI-Proxy-Telegram-ID"
    )
    try:
        tgid = int(telegram_raw) if telegram_raw else None
    except ValueError:
        return None
    return (
        operator_id,
        request.headers.get("X-MAGI-Proxy-Operator-Name", "WebUI operator"),
        tgid,
    )


def verified_proxy_scope(
    bus, request: Request
) -> tuple[bool, bool, bool, int | None] | None:
    """Return the signed MAGIS-admin / local-assigned capabilities."""
    if verified_proxy_operator(bus, request) is None:
        return None
    try:
        parts = dict(item.split("=", 1) for item in request.headers["X-MAGI-Proxy-Scope"].split(";"))
        is_admin = parts["admin"] == "1"
        is_assigned = parts["assigned"] == "1"
        magis_admin_id = int(parts["magis_admin_id"])
        two_factor = parts["two_factor"] == "1"
    except (KeyError, ValueError):
        return None
    if is_admin != (magis_admin_id > 0):
        return None
    if parts["two_factor"] not in {"0", "1"}:
        return None
    return is_admin, is_assigned, two_factor, magis_admin_id or None


def ensure_runtime_operator(request: Request) -> int | None:
    """Materialise the authenticated control operator in this MAGI's SQLite.

    Contacts are private MAGI data, so their numeric IDs are not global.  A
    verified control request is mapped by Telegram identity when available; an
    explicit system marker covers WebUI-only operators.  The returned local
    contact ID keeps existing chat/conversation APIs correctly scoped.
    """
    from magi.channels.api.dependencies import get_bus

    bus = get_bus(request)
    identity = verified_proxy_operator(bus, request)
    if identity is None:
        return None
    _operator_id, name, _tgid = identity
    scope = verified_proxy_scope(bus, request)
    if scope is None:
        return None
    is_admin, _is_assigned, _two_factor, magis_admin_id = scope
    if not is_admin or magis_admin_id is None:
        return None

    projection = bus.contacts_book.ensure_magis_admin_projection(
        magis_admin_id=magis_admin_id,
        display_name=name,
    )
    return projection.id


__all__ = [
    "build_proxy_headers",
    "ensure_runtime_operator",
    "resolve_control_secret",
    "verified_proxy_operator",
    "verified_proxy_scope",
]
