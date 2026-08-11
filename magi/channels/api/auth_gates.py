"""Shared auth gate dependencies for admin-gated routes.

Extracted from the old ``contacts.py`` so that routers
(``magi``, ``magis``, ``contacts``, ``soul``, ``tasks``,
...) can import the same ``admin_gate`` and
``admin_or_assigned_gate`` without circular imports.

D.24: the ``magi_session`` cookie carries the ``contact_id``
(Contact PK), not a chat id.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request

from magi.channels.api.dependencies import get_bus
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.auth_gates")


def _is_admin_contact_id(bus, contact_id: int) -> bool:
    """True iff the local Contact row at ``contact_id`` is a WebUI admin.

    ``proxied_contact_id`` returned by :func:`ensure_runtime_operator` is
    always a local Contact id (the proxy materialises the
    control-plane operator into this runtime's own contacts table
    before forwarding the request), so the same lookup works on
    both code paths. We no longer branch on
    ``control_store.enabled()`` here: a control-plane deployment
    without a local contact row will simply deny the request,
    which is the safer failure mode than asking the runtime to
    consult the unreachable MAGIS store.
    """
    try:
        c = bus.contacts_book.get(contact_id=contact_id)
        # ``admin`` is the WebUI sign-in bit. Independent
        # of ``role`` — an assigned user with admin=True
        # is their own operator; a contact with admin=True
        # is the classic operator role. The split
        # replaces the pre-2024 ``role == 'admin'``
        # check.
        return c is not None and c.admin
    except Exception:
        logger.exception("admin_gate: ORM read failed; denying access")
        return False


def _resolve_contact_id(bus, raw: str | None) -> int | None:
    """Verify the signed session cookie, return contact_id or None.

    Reads the v3 ``_sign_selected_session`` cookie first and
    falls back to the legacy ``_sign_contact_id`` cookie
    during the deprecation window. Returns ``None`` if the
    cookie is missing, malformed, expired, or its contact
    is no longer an admin.
    """
    from magi.channels.api.auth import resolve_session

    session = resolve_session(bus, raw)
    if session is None:
        return None
    return int(session["contact_id"])


def admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is an admin."""
    # A selected MAGI's Runtime API is never browser-facing. The one WebUI
    # service forwards authenticated requests with a short-lived HMAC proof;
    # map that global operator to this runtime's private Contact identity.
    from magi.channels.api.proxy_auth import ensure_runtime_operator

    proxied_contact_id = ensure_runtime_operator(request)
    if proxied_contact_id is not None:
        if _is_admin_contact_id(get_bus(request), proxied_contact_id):
            return str(proxied_contact_id)
        raise MagiHTTPException(
            status_code=403,
            code="auth.magis_admin_required",
            detail="This action requires a MAGIS administrator",
        )
    raw = request.cookies.get("magi_session")
    contact_id = _resolve_contact_id(get_bus(request), raw)
    if contact_id is None or not _is_admin_contact_id(get_bus(request), contact_id):
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    return str(contact_id)


AdminGate = Annotated[str, Depends(admin_gate)]


def admin_or_assigned_gate(request: Request) -> str:
    """FastAPI dependency — admin or assigned contact.

    Accepts the caller if EITHER:
      - the ``admin`` bit is True (WebUI operator), OR
      - the ``role`` is ``'assigned'`` (the served user —
        who may not have backend access but can still edit
        their own SOUL.md).

    The old check (``role in ("admin", "assigned")``) is
    replaced because admin is now a separate boolean — a
    contact with ``role='assigned', admin=True`` is a valid
    operator who should pass this gate; a contact with
    ``role='assigned', admin=False`` is the served user who
    should also pass.
    """
    from magi.channels.api.proxy_auth import ensure_runtime_operator

    proxied_contact_id = ensure_runtime_operator(request)
    if proxied_contact_id is not None:
        contact = get_bus(request).contacts_book.get(contact_id=proxied_contact_id)
        if contact is not None and (contact.admin or contact.role == "assigned"):
            return str(proxied_contact_id)
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="This action requires node access",
        )

    raw = request.cookies.get("magi_session") or ""
    contact_id = _resolve_contact_id(get_bus(request), raw)
    if contact_id is None:
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="SOUL.md editing requires admin or assigned role",
        )
    try:
        c = get_bus(request).contacts_book.get(contact_id=contact_id)
    except Exception:
        logger.exception("admin_or_assigned_gate: ORM read failed")
        raise MagiHTTPException(  # noqa: B904
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="internal error verifying role",
        )
    if c is None or not (c.admin or c.role == "assigned"):
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="SOUL.md editing requires admin or assigned role",
        )
    return str(contact_id)


AdminOrAssignedGate = Annotated[str, Depends(admin_or_assigned_gate)]


__all__ = [
    "admin_gate",
    "AdminGate",
    "admin_or_assigned_gate",
    "AdminOrAssignedGate",
]
