"""Admin endpoint to bind / unbind a Telegram chat_id to an
employee.

C2 lands the proper flow (employee /start's the bot with a
6-digit code, code is verified, chat_id written). For v0
the operator types the chat_id into a form on the employee
detail panel (or hits this endpoint directly with curl);
C2 will reuse the same ``_set_user_employee_id`` helper and
just replace the manual input with a code-based handshake.

Storage lives in the ``meta`` table under
``telegram.user.<chat_id>.employee_id`` — see
:func:`magi.channels.telegram.bot._get_user_employee_id` for
the read path. The key is a stringified int so we don't
need to think about JSON encoding.
"""

from __future__ import annotations

import os
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from magi.channels.telegram.bot import _set_user_employee_id
from magi.channels.webui.api.departments import AdminGate
from magi.runtime.state.orm import Employee, open_session

router = APIRouter(tags=["telegram"])


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


class TGBindRequest(BaseModel):
    """Body for ``POST /api/telegram/bind``.

    ``employee_id`` is the row in ``employees`` that the
    chat_id should map to. ``chat_id`` is the Telegram chat
    id (must be numeric — TG chat ids are always integers).
    Both fields are required.
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

    Validates that the employee exists and is active (not
    separated), then writes the meta key. Returns the
    binding so the UI can show the operator the saved
    mapping without a re-fetch.
    """
    if not payload.chat_id.lstrip("-").isdigit():
        # TG chat ids are always integers (possibly negative
        # for group chats). Reject anything that isn't digits
        # so a typo "abc" doesn't get silently written.
        raise HTTPException(
            status_code=400,
            detail="chat_id must be a numeric Telegram chat id",
        )

    sd = _state_dir()
    with open_session() as session:
        emp = session.get(Employee, payload.employee_id)
        if emp is None:
            raise HTTPException(
                status_code=404,
                detail=f"employee {payload.employee_id} not found",
            )
        if emp.separated_at is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"employee {emp.name!r} is marked separated; "
                    "restore them before binding a chat_id"
                ),
            )

    _set_user_employee_id(sd, payload.chat_id, payload.employee_id)
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
    returns 204 with no error, so the UI can use the same
    call to handle "user clicked unbind on an already-
    unbound row".
    """
    if not chat_id.lstrip("-").isdigit():
        raise HTTPException(
            status_code=400,
            detail="chat_id must be a numeric Telegram chat id",
        )
    _set_user_employee_id(_state_dir(), chat_id, None)
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
    "unbind" confirmation with the employee name. The
    bound employee is resolved by id so the name shows up
    even if the row's been soft-deleted (a deliberate UX
    choice — the operator should still see "this chat was
    bound to X" when unbinding).
    """
    from magi.channels.telegram.bot import _get_user_employee_id

    if not chat_id.lstrip("-").isdigit():
        raise HTTPException(
            status_code=400,
            detail="chat_id must be a numeric Telegram chat id",
        )

    sd = _state_dir()
    raw = _get_user_employee_id(sd, chat_id)
    if raw is None:
        return TGBindStatus(chat_id=chat_id, bound_employee_id=None)

    try:
        emp_id = int(raw)
    except (TypeError, ValueError):
        return TGBindStatus(chat_id=chat_id, bound_employee_id=None)

    with open_session() as session:
        emp = session.get(Employee, emp_id)
        name = emp.name if emp is not None else None

    return TGBindStatus(
        chat_id=chat_id,
        bound_employee_id=emp_id,
        bound_employee_name=name,
    )
