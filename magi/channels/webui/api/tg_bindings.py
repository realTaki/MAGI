"""TG-specific binding admin endpoints (D.28).

Routes:
  POST   /api/telegram/bind                  — bind a TG chat id to an employee
  DELETE /api/telegram/bind/{telegram_id}    — unbind a TG chat id
  GET    /api/telegram/bind/{telegram_id}    — look up the current binding

All three operate on the channel dispatcher (D.28). The
endpoint code here is just HTTP shape + admin gating; the
actual write logic is in
:meth:`magi.channels.telegram.adapter.TelegramAdapter.bind_im_id` /
``unbind_im_id`` / ``lookup_im_id`` — which writes both
``user_im_bindings`` (the canonical store) and the legacy
``Employee.telegram_id`` column (read-cache, kept for the
bot's inbound path).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.agent.db import Employee, open_session
from magi.channels import dispatcher as channel_dispatcher
from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException

router = APIRouter(tags=["telegram"])


class TGBindRequest(BaseModel):
    """Body for ``POST /api/telegram/bind``.

    ``uid`` is the row in ``employees`` to bind to.
    ``telegram_id`` is the TG chat id (numeric). Both
    required.
    """

    telegram_id: str = Field(min_length=1, max_length=32)
    uid: int = Field(ge=1)


class TGBindResponse(BaseModel):
    telegram_id: str
    uid: int


@router.post("/telegram/bind", response_model=TGBindResponse)
def bind_telegram(
    payload: TGBindRequest,
    _admin: AdminGate,
) -> TGBindResponse:
    """Bind ``telegram_id`` to ``uid``.

    Delegates the actual write to the channel dispatcher
    (which calls the TG adapter). The API enforces the
    "employee is active" + "unbind previous holder" rules
    that are policy concerns, not channel concerns.
    """
    if not payload.telegram_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must be a numeric Telegram chat id",
        )
    try:
        tgid_int = int(payload.telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
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
                    "restore them before binding a TG chat"
                ),
            )

        # Unbind whatever currently has this tgid (if any).
        # The unique constraint on ``telegram_id`` would raise
        # on commit if we skipped this; doing it explicitly
        # gives a cleaner error and a clear log line.
        existing = session.scalar(
            select(Employee).where(Employee.telegram_id == tgid_int)
        )
        if existing is not None and existing.id != emp.id:
            existing.telegram_id = None
            session.flush()

        # Hand the actual write to the channel dispatcher
        # (D.28). The adapter writes ``user_im_bindings``
        # AND syncs ``Employee.telegram_id`` (the read-
        # cache the bot's inbound handler still uses).
        channel_dispatcher.bind_im_id(emp.id, "tg", str(tgid_int))
        session.refresh(emp)  # pick up the legacy column write-back
        session.commit()

    return TGBindResponse(
        telegram_id=payload.telegram_id,
        uid=payload.uid,
    )


@router.delete(
    "/telegram/bind/{telegram_id}",
    status_code=204,
    response_class=Response,
)
def unbind_telegram(
    telegram_id: str,
    _admin: AdminGate,
) -> Response:
    """Clear the binding for ``telegram_id``.

    Idempotent — unbinding an already-unbound chat id
    returns 204 with no error so the UI can use the same
    call to handle "user clicked unbind on an already-
    unbound row".
    """
    if not telegram_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must be a numeric Telegram chat id",
        )
    try:
        tgid_int = int(telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    # The dispatcher resolves the bound uid and the
    # adapter drops both the new and legacy rows.
    with open_session() as session:
        bound_emp = session.scalar(
            select(Employee).where(Employee.telegram_id == tgid_int)
        )
    if bound_emp is not None:
        channel_dispatcher.unbind_im_id(bound_emp.id)
    return Response(status_code=204)


class TGBindStatus(BaseModel):
    telegram_id: str
    bound_uid: int | None
    bound_employee_name: str | None = None


@router.get(
    "/telegram/bind/{telegram_id}",
    response_model=TGBindStatus,
)
def get_telegram_binding(
    telegram_id: str,
    _admin: AdminGate,
) -> TGBindStatus:
    """Return the current binding (if any) for ``telegram_id``.

    The operator-facing UI uses this to pre-fill the
    "unbind" confirmation with the employee name. Even
    if the bound row is gone (deleted via the WebUI), the
    endpoint reports ``bound_uid`` so the operator
    can see the dangling reference and re-bind or clean
    it up explicitly.
    """
    if not telegram_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must be a numeric Telegram chat id",
        )
    try:
        tgid_int = int(telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    bound_uid = None
    bound_name = None
    with open_session() as session:
        emp = session.scalar(
            select(Employee).where(Employee.telegram_id == tgid_int)
        )
        if emp is not None:
            bound_uid = emp.id
            bound_name = emp.name
    return TGBindStatus(
        telegram_id=telegram_id,
        bound_uid=bound_uid,
        bound_employee_name=bound_name,
    )
