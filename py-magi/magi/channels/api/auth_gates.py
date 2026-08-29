"""Route-level identity checks.

MAGIS administrator authority is verified from the shared administrator
identity carried by the session or signed runtime proxy request.  Local
``Contact`` rows never carry an administrator bit.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from magi.old_bus.firmwares.books.magis import AUTH_MODE_DISABLED
from magi.old_bus.firmwares.books.local import Role
from magi.channels.api.dependencies import get_bus
from magi.channels.api.errors import MagiHTTPException


def _session_contact_id(request: Request) -> int | None:
    from magi.channels.api.auth import resolve_session

    session = resolve_session(get_bus(request), request.cookies.get("magi_session"))
    if session is None:
        return None
    return int(session["contact_id"])


def _session_is_current_admin(request: Request) -> bool:
    from magi.channels.api.auth import resolve_session

    bus = get_bus(request)
    session = resolve_session(bus, request.cookies.get("magi_session"))
    if session is None or not session.get("admin"):
        return False
    admin_id = session.get("magis_admin_id")
    if not isinstance(admin_id, int) or bus.magis_admins_book is None:
        return False
    admin = bus.magis_admins_book.get(admin_id)
    if admin is None or admin.auth_mode == AUTH_MODE_DISABLED:
        return False
    memberships = bus.memberships_book
    membership = memberships.get(int(session["magi_id"])) if memberships else None
    return membership is not None and membership.magis_id == admin.magis_id


def _proxy_identity(request: Request) -> tuple[int, bool] | None:
    """Return ``(local_contact_id, is_admin)`` for a signed runtime call."""
    from magi.channels.api.proxy_auth import ensure_runtime_operator, verified_proxy_operator, verified_proxy_scope

    bus = get_bus(request)
    if verified_proxy_operator(bus, request) is None:
        return None
    scope = verified_proxy_scope(bus, request)
    if scope is None:
        return None
    is_admin, is_assigned, _two_factor, admin_id = scope
    if is_admin:
        admin = bus.magis_admins_book.get(admin_id) if (admin_id and bus.magis_admins_book) else None
        runtime_id_raw = request.headers.get("X-MAGI-Proxy-Target")
        membership = (
            bus.memberships_book.get(int(runtime_id_raw))
            if runtime_id_raw and runtime_id_raw.isdigit() and bus.memberships_book
            else None
        )
        if admin is None or admin.auth_mode == AUTH_MODE_DISABLED or membership is None or membership.magis_id != admin.magis_id:
            return None
        contact_id = ensure_runtime_operator(request)
        return (contact_id, True) if contact_id is not None else None
    if is_assigned:
        # Assigned sessions are target-bound by the HMAC.  Their operator id
        # is their existing local Contact id, not a MAGIS authority identity.
        operator = verified_proxy_operator(bus, request)
        if operator is None:
            return None
        contact = get_bus(request).contacts_book.get(operator[0])
        return (contact.id, False) if contact is not None and contact.role == Role.ASSIGNED else None
    return None


def admin_gate(request: Request) -> str:
    proxy = _proxy_identity(request)
    if proxy is not None:
        contact_id, is_admin = proxy
        if is_admin:
            return str(contact_id)
        raise MagiHTTPException(403, "auth.magis_admin_required", "MAGIS administrator required")
    if not _session_is_current_admin(request):
        raise MagiHTTPException(401, "auth.not_signed_in", "Not signed in")
    contact_id = _session_contact_id(request)
    if contact_id is None:
        raise MagiHTTPException(401, "auth.not_signed_in", "Not signed in")
    return str(contact_id)


AdminGate = Annotated[str, Depends(admin_gate)]


def admin_or_assigned_gate(request: Request) -> str:
    proxy = _proxy_identity(request)
    if proxy is not None:
        return str(proxy[0])
    if _session_is_current_admin(request):
        contact_id = _session_contact_id(request)
        if contact_id is not None:
            return str(contact_id)
    contact_id = _session_contact_id(request)
    if contact_id is not None:
        contact = get_bus(request).contacts_book.get(contact_id)
        if contact is not None and contact.role == Role.ASSIGNED:
            return str(contact_id)
    raise MagiHTTPException(403, "auth.soul_edit_forbidden", "MAGIS administrator or assigned user required")


AdminOrAssignedGate = Annotated[str, Depends(admin_or_assigned_gate)]

__all__ = ["AdminGate", "AdminOrAssignedGate", "admin_gate", "admin_or_assigned_gate"]
