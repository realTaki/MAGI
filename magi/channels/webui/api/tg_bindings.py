"""Admin endpoints to bind / unbind a Telegram tgid to an
employee.

C2 lands the proper self-serve flow (employee /start's the
bot with a 6-digit code; on success the tgid is written
to ``Employee.telegram_id``). For v0 the operator drives the
binding manually — pick a tgid and an employee, hit
``POST /api/telegram/bind``, and the row's ``telegram_id``
gets set. The same PATCH can also happen directly via
``PATCH /api/employees/{id}`` with ``{"telegram_id": "..."}``;
this router is a thin convenience that does the tgid
lookup in the other direction (tgid → employee).

Storage lives on the employee row (``Employee.telegram_id``,
unique across the table). The legacy ``telegram.user.<tgid>.uid``
meta key is still read by the TG bot as a fallback for
un-migrated state but is no longer written.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Response

from magi.channels.webui.api.errors import MagiHTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.channels.webui.api.departments import AdminGate
from magi.agent.db import Employee, open_session, require_state_dir

router = APIRouter(tags=["telegram"])


def _state_dir() -> str:
    return require_state_dir()


class TGBindRequest(BaseModel):
    """Body for ``POST /api/telegram/bind``.

    ``uid`` is the row in ``employees`` to bind to.
    ``tgid`` is the Telegram chat id (numeric). Both are
    required.
    """

    tgid: str = Field(min_length=1, max_length=32)
    uid: int = Field(ge=1)


class TGBindResponse(BaseModel):
    tgid: str
    uid: int


@router.post("/telegram/bind", response_model=TGBindResponse)
def bind_telegram(
    payload: TGBindRequest,
    _admin: AdminGate,
) -> TGBindResponse:
    """Bind ``tgid`` to ``uid``.

    Writes ``Employee.telegram_id`` (and un-binds whatever
    row currently has that tgid, so the binding is
    one-to-one). Validates the employee is active (not
    separated); separating an employee is the operator's
    way of pausing their EVE without losing history.
    """
    if not payload.tgid.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="tgid must be a numeric Telegram chat id",
        )
    try:
        tgid_int = int(payload.tgid)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="tgid must fit in an integer",
        )

    with open_session() as session:
        emp = session.get(Employee, payload.uid)
        if emp is None:
            raise MagiHTTPException(
                status_code=404,
                code="not_found.employee",
                detail=f"employee {payload.uid} not found",
            )
        if emp.separated_at is not None:
            raise MagiHTTPException(
                status_code=409,
                code="conflict.employee_separated",
                detail=(
                    f"employee {emp.name!r} is marked separated; "
                    "restore them before binding a tgid"
                ),
            )

        # Unbind whatever currently has this tgid (if
        # any). The unique constraint on telegram_id will
        # raise on commit if we skip this, but doing it
        # explicitly gives a cleaner error and a clear
        # log line. ``session.flush()`` between the clear
        # and the new bind is needed because the unique
        # index is checked row-by-row at flush time —
        # without the explicit flush, SQLAlchemy may
        # apply the new UPDATE before the clear, hitting
        # the unique constraint on the old holder.
        existing = session.scalar(
            select(Employee).where(Employee.telegram_id == tgid_int)
        )
        if existing is not None and existing.id != emp.id:
            existing.telegram_id = None
            session.flush()

        emp.telegram_id = tgid_int
        session.commit()

    return TGBindResponse(
        tgid=payload.tgid,
        uid=payload.uid,
    )


@router.delete(
    "/telegram/bind/{tgid}",
    status_code=204,
    response_class=Response,
)
def unbind_telegram(
    tgid: str,
    _admin: AdminGate,
) -> Response:
    """Clear the binding for ``tgid``.

    Idempotent — unbinding an already-unbound tgid
    returns 204 with no error so the UI can use the same
    call to handle "user clicked unbind on an already-
    unbound row".
    """
    if not tgid.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="tgid must be a numeric Telegram chat id",
        )
    try:
        tgid_int = int(tgid)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="tgid must fit in an integer",
        )
    with open_session() as session:
        emp = session.scalar(
            select(Employee).where(Employee.telegram_id == tgid_int)
        )
        if emp is not None:
            emp.telegram_id = None
            session.commit()
    return Response(status_code=204)


class TGBindStatus(BaseModel):
    tgid: str
    bound_uid: int | None
    bound_employee_name: str | None = None


@router.get(
    "/telegram/bind/{tgid}",
    response_model=TGBindStatus,
)
def get_telegram_binding(
    tgid: str,
    _admin: AdminGate,
) -> TGBindStatus:
    """Return the current binding (if any) for ``tgid``.

    The operator-facing UI uses this to pre-fill the
    "unbind" confirmation with the employee name. Even
    if the bound row is gone (deleted via the WebUI), the
    endpoint reports ``bound_uid`` so the operator
    can see the dangling reference and re-bind or clean
    it up explicitly.
    """
    if not tgid.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="tgid must be a numeric Telegram chat id",
        )
    try:
        tgid_int = int(tgid)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="tgid must fit in an integer",
        )

    with open_session() as session:
        emp = session.scalar(
            select(Employee).where(Employee.telegram_id == tgid_int)
        )
        if emp is None:
            return TGBindStatus(tgid=tgid, bound_uid=None)
        return TGBindStatus(
            tgid=tgid,
            bound_uid=emp.id,
            bound_employee_name=emp.name,
        )
