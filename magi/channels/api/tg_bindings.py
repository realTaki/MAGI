"""TG-specific binding admin endpoints (D.28).

Routes:
  POST   /api/telegram/bind                  — bind a TG chat id to a contact
  DELETE /api/telegram/bind/{telegram_id}    — unbind a TG chat id
  GET    /api/telegram/bind/{telegram_id}    — look up the current binding

All three use ``ContactBook.telegram_id`` as the canonical Telegram
delivery address. The endpoint code is HTTP shape + admin gating;
the Book owns the durable read/write operations.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

router = APIRouter(tags=["telegram"])


class TGBindRequest(BaseModel):
    """Body for ``POST /api/telegram/bind``.

    ``contact_id`` is the row in ``contacts`` to bind to.
    ``telegram_id`` is the TG chat id (numeric). Both
    required.
    """

    telegram_id: str = Field(min_length=1, max_length=32)
    contact_id: int = Field(ge=1)


class TGBindResponse(BaseModel):
    telegram_id: str
    contact_id: int


@router.post("/telegram/bind", response_model=TGBindResponse)
def bind_telegram(
    payload: TGBindRequest,
    _admin: AdminGate,
    bus: BusDep,
) -> TGBindResponse:
    """Bind ``telegram_id`` to ``contact_id``.

    The API writes the Contact-owned address and enforces the
    "contact is active" + "unbind previous holder" rules
    that are policy concerns, not channel concerns.
    """
    if not payload.telegram_id.lstrip("-").isdigit():
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must be a numeric Telegram chat id",
        )
    try:
        telegram_id_int = int(payload.telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    contact = bus.contacts_book.get(contact_id=payload.contact_id)
    if contact is None:
        raise MagiHTTPException(status_code=404, code="not_found.contact", detail=f"contact {payload.contact_id} not found")
    bus.contacts_book.set_telegram_id(contact_id=payload.contact_id, telegram_id=telegram_id_int)

    return TGBindResponse(
        telegram_id=payload.telegram_id,
        contact_id=payload.contact_id,
    )


@router.delete(
    "/telegram/bind/{telegram_id}",
    status_code=204,
    response_class=Response,
)
def unbind_telegram(
    telegram_id: str,
    _admin: AdminGate,
    bus: BusDep,
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
        telegram_id_int = int(telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    # ContactBook resolves the bound contact and clears the address.
    contact = bus.contacts_book.get_by_telegram(telegram_id=telegram_id_int)
    if contact is not None:
        bus.contacts_book.set_telegram_id(contact_id=contact.id, telegram_id=None)
    return Response(status_code=204)


class TGBindStatus(BaseModel):
    telegram_id: str
    bound_contact_id: int | None
    bound_contact_name: str | None = None


@router.get(
    "/telegram/bind/{telegram_id}",
    response_model=TGBindStatus,
)
def get_telegram_binding(
    telegram_id: str,
    _admin: AdminGate,
    bus: BusDep,
) -> TGBindStatus:
    """Return the current binding (if any) for ``telegram_id``.

    The operator-facing UI uses this to pre-fill the
    "unbind" confirmation with the contact name. Even
    if the bound row is gone (deleted via the WebUI), the
    endpoint reports ``bound_contact_id`` so the operator
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
        telegram_id_int = int(telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    bound_contact_id = None
    bound_name = None
    contact = bus.contacts_book.get_by_telegram(telegram_id=telegram_id_int)
    if contact is not None:
        bound_contact_id = contact.id
        bound_name = contact.name
    return TGBindStatus(
        telegram_id=telegram_id,
        bound_contact_id=bound_contact_id,
        bound_contact_name=bound_name,
    )
