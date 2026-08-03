"""Shared auth gate dependencies for admin-gated routes.

Extracted from the old ``contacts.py`` so that routers
(``magic``, ``magis``, ``contacts``, ``soul``, ``tasks``,
...) can import the same ``admin_gate`` and
``admin_or_assigned_gate`` without circular imports.

D.24: the ``magi_session`` cookie carries the ``uid``
(Contact PK), not a chat id.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request

from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.auth_gates")


def _is_admin_uid(uid: int) -> bool:
    from magi.channels.webui import control_store
    if control_store.enabled():
        from magi.bus import bootstrap
        from magi.constants import STATE_DIR
        import os
        return bootstrap(os.environ.get("MAGI_STATE_DIR", STATE_DIR)).magis.is_control_admin(uid)
    try:
        from magi.bus import bootstrap
        from magi.constants import STATE_DIR
        import os
        c = bootstrap(os.environ.get("MAGI_STATE_DIR", STATE_DIR)).contacts.get(uid)
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


def _resolve_uid(raw: str | None) -> int | None:
    """Verify the signed session cookie, return uid or None."""
    from magi.channels.webui.api.auth import _verify_signed_uid
    return _verify_signed_uid(raw or "")


def admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is an admin."""
    # A selected MAGI's Runtime API is never browser-facing. The one WebUI
    # service forwards authenticated requests with a short-lived HMAC proof;
    # map that global operator to this runtime's private Contact identity.
    from magi.channels.webui.proxy_auth import ensure_runtime_operator

    proxied_uid = ensure_runtime_operator(request)
    if proxied_uid is not None:
        if _is_admin_uid(proxied_uid):
            return str(proxied_uid)
        raise MagiHTTPException(
            status_code=403, code="auth.magis_admin_required", detail="This action requires a MAGIS administrator"
        )
    raw = request.cookies.get("magi_session")
    uid = _resolve_uid(raw)
    if uid is None or not _is_admin_uid(uid):
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    return str(uid)


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
    from magi.channels.webui.proxy_auth import ensure_runtime_operator

    proxied_uid = ensure_runtime_operator(request)
    if proxied_uid is not None:
        from magi.bus import bootstrap
        from magi.constants import STATE_DIR
        import os
        contact = bootstrap(os.environ.get("MAGI_STATE_DIR", STATE_DIR)).contacts.get(proxied_uid)
        if contact is not None and (contact.admin or contact.role == "assigned"):
            return str(proxied_uid)
        raise MagiHTTPException(status_code=403, code="auth.soul_edit_forbidden", detail="This action requires node access")

    raw = request.cookies.get("magi_session") or ""
    uid = _resolve_uid(raw)
    if uid is None:
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="SOUL.md editing requires admin or assigned role",
        )
    try:
        from magi.bus import bootstrap
        from magi.constants import STATE_DIR
        import os
        c = bootstrap(os.environ.get("MAGI_STATE_DIR", STATE_DIR)).contacts.get(uid)
    except Exception:
        logger.exception("admin_or_assigned_gate: ORM read failed")
        raise MagiHTTPException(
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
    return str(uid)


AdminOrAssignedGate = Annotated[str, Depends(admin_or_assigned_gate)]


__all__ = [
    "admin_gate",
    "AdminGate",
    "admin_or_assigned_gate",
    "AdminOrAssignedGate",
]
