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


def _canonical(method: str, path_and_query: str, timestamp: str, target_id: str, operator_id: str) -> bytes:
    return "\n".join((method.upper(), path_and_query, timestamp, target_id, operator_id)).encode()


def build_proxy_headers(*, method: str, path_and_query: str, target_id: int, operator_id: int, operator_name: str, telegram_id: int | None) -> dict[str, str]:
    """Return service-to-service headers for one selected MAGI request."""
    secret = _secret()
    if secret is None:
        raise RuntimeError("MAGI_CONTROL_SECRET is required for WebUI runtime proxying")
    timestamp = str(int(time.time()))
    target = str(target_id)
    operator = str(operator_id)
    signature = hmac.new(
        secret,
        _canonical(method, path_and_query, timestamp, target, operator),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-MAGI-Proxy-Timestamp": timestamp,
        "X-MAGI-Proxy-Target": target,
        "X-MAGI-Proxy-Operator": operator,
        "X-MAGI-Proxy-Operator-Name": operator_name[:120],
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
    expected = hmac.new(
        secret,
        _canonical(request.method, path, timestamp, target, operator),
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
    from sqlalchemy import select

    from magi.agent.db import Contact, open_session
    from magi.agent.db.models_contact import SOURCE_SYSTEM

    marker = f"magi.control_operator_id={operator_id}"
    with open_session() as session:
        if telegram_id is not None:
            contact = session.scalar(select(Contact).where(Contact.telegram_id == telegram_id))
        else:
            contact = session.scalar(
                select(Contact).where(Contact.source == SOURCE_SYSTEM, Contact.notes == marker)
            )
        if contact is None:
            contact = Contact(
                name=name or f"WebUI operator {operator_id}",
                display_name=name or None,
                role="assigned",
                admin=True,
                telegram_id=telegram_id,
                source=SOURCE_SYSTEM,
                notes=marker,
            )
            session.add(contact)
            session.commit()
            session.refresh(contact)
        elif not contact.admin:
            contact.admin = True
            session.commit()
        return contact.id


__all__ = ["build_proxy_headers", "ensure_runtime_operator", "verified_proxy_operator"]
