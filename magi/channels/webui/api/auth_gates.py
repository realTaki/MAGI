"""Shared auth gate dependencies for admin-gated routes.

Extracted from the old ``employees.py`` so that routers
(``magics``, ``magis``, ``contacts``, ``soul``, ``tasks``,
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
    from magi.agent.db import Contact, open_session

    try:
        with open_session() as session:
            c = session.get(Contact, uid)
            return c is not None and c.role == "admin"
    except Exception:
        logger.exception("admin_gate: ORM read failed; denying access")
        return False


def _resolve_uid(raw: str | None) -> int | None:
    """Verify the signed session cookie, return uid or None."""
    from magi.channels.webui.api.auth import _verify_signed_uid
    return _verify_signed_uid(raw or "")


def admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is an admin."""
    raw = request.cookies.get("magi_session")
    uid = _resolve_uid(raw)
    if uid is None or not _is_admin_uid(uid):
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    return str(uid)


AdminGate = Annotated[str, Depends(admin_gate)]


def admin_or_assigned_gate(request: Request) -> str:
    """FastAPI dependency — admin or assigned contact."""
    from magi.agent.db import Contact, open_session

    raw = request.cookies.get("magi_session") or ""
    uid = _resolve_uid(raw)
    if uid is None:
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="SOUL.md editing requires admin or assigned role",
        )
    try:
        with open_session() as session:
            c = session.get(Contact, uid)
    except Exception:
        logger.exception("admin_or_assigned_gate: ORM read failed")
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="internal error verifying role",
        )
    if c is None or c.role not in ("admin", "assigned"):
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
