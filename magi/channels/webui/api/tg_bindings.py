"""Admin endpoints to bind / unbind a Telegram chat_id to an
employee.

C2 lands the proper self-serve flow (employee /start's the
bot with a 6-digit code; on success the chat_id is written
to ``Employee.telegram_id``). For v0 the operator drives the
binding manually — pick a chat_id and an employee, hit
``POST /api/telegram/bind``, and the row's ``telegram_id``
gets set. The same PATCH can also happen directly via
``PATCH /api/employees/{id}`` with ``{"telegram_id": "..."}``;
this router is a thin convenience that does the chat_id
lookup in the other direction (chat_id → employee).

Storage lives on the employee row (``Employee.telegram_id``,
unique across the table). The legacy ``telegram.user.<chat_id>.employee_id``
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
from magi.agent.db import Employee, open_session

router = APIRouter(tags=["telegram"])


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


class TGBindRequest(BaseModel):
    """Body for ``POST /api/telegram/bind``.

    ``employee_id`` is the row in ``employees`` to bind to.
    ``chat_id`` is the Telegram chat id (numeric). Both are
    required.
    """

    chat_id: str = Field(min_length=1, max_length=32)
    employee_id: int = Field(ge=1)


class TGBindResponse(BaseModel):
    chat_id: str
    employee_id: int


@router.post("/telegram/bind", response_model=TGBindResponse)
def bind_telegram(
    payload: TGBindRequest,
    _admin: AdminGate,
) -> TGBindResponse:
    """Bind ``chat_id`` to ``employee_id``.

    Writes ``Employee.telegram_id`` (and un-binds whatever
    row currently has that chat_id, so the binding is
    one-to-one). Validates the employee is active (not
    separated); separating an employee is the operator's
    way of pausing their EVE without losing history.
    """
    if not payload.chat_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="chat_id must be a numeric Telegram chat id",
        )
    try:
        chat_id_int = int(payload.chat_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="chat_id must fit in an integer",
        )

    with open_session() as session:
        emp = session.get(Employee, payload.employee_id)
        if emp is None:
            raise MagiHTTPException(
                status_code=404,
                code="not_found.employee",
                detail=f"employee {payload.employee_id} not found",
            )
        if emp.separated_at is not None:
            raise MagiHTTPException(
                status_code=409,
                code="conflict.employee_separated",
                detail=(
                    f"employee {emp.name!r} is marked separated; "
                    "restore them before binding a chat_id"
                ),
            )

        # Unbind whatever currently has this chat_id (if
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
            select(Employee).where(Employee.telegram_id == chat_id_int)
        )
        if existing is not None and existing.id != emp.id:
            existing.telegram_id = None
            session.flush()

        emp.telegram_id = chat_id_int
        session.commit()

    return TGBindResponse(
        chat_id=payload.chat_id,
        employee_id=payload.employee_id,
    )


@router.delete(
    "/telegram/bind/{chat_id}",
    status_code=204,
    response_class=Response,
)
def unbind_telegram(
    chat_id: str,
    _admin: AdminGate,
) -> Response:
    """Clear the binding for ``chat_id``.

    Idempotent — unbinding an already-unbound chat_id
    returns 204 with no error so the UI can use the same
    call to handle "user clicked unbind on an already-
    unbound row".
    """
    if not chat_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="chat_id must be a numeric Telegram chat id",
        )
    try:
        chat_id_int = int(chat_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="chat_id must fit in an integer",
        )
    with open_session() as session:
        emp = session.scalar(
            select(Employee).where(Employee.telegram_id == chat_id_int)
        )
        if emp is not None:
            emp.telegram_id = None
            session.commit()
    return Response(status_code=204)


class TGBindStatus(BaseModel):
    chat_id: str
    bound_employee_id: int | None
    bound_employee_name: str | None = None


@router.get(
    "/telegram/bind/{chat_id}",
    response_model=TGBindStatus,
)
def get_telegram_binding(
    chat_id: str,
    _admin: AdminGate,
) -> TGBindStatus:
    """Return the current binding (if any) for ``chat_id``.

    The operator-facing UI uses this to pre-fill the
    "unbind" confirmation with the employee name. Even
    if the bound row is gone (deleted via the WebUI), the
    endpoint reports ``bound_employee_id`` so the operator
    can see the dangling reference and re-bind or clean
    it up explicitly.
    """
    if not chat_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="chat_id must be a numeric Telegram chat id",
        )
    try:
        chat_id_int = int(chat_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="chat_id must fit in an integer",
        )

    with open_session() as session:
        emp = session.scalar(
            select(Employee).where(Employee.telegram_id == chat_id_int)
        )
        if emp is None:
            return TGBindStatus(chat_id=chat_id, bound_employee_id=None)
        return TGBindStatus(
            chat_id=chat_id,
            bound_employee_id=emp.id,
            bound_employee_name=emp.name,
        )
