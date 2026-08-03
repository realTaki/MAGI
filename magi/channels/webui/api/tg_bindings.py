"""TG-specific binding admin endpoints (D.28).

Routes:
  POST   /api/telegram/bind                  — bind a TG chat id to a contact
  DELETE /api/telegram/bind/{telegram_id}    — unbind a TG chat id
  GET    /api/telegram/bind/{telegram_id}    — look up the current binding

All three operate on the channel dispatcher (D.28). The
endpoint code here is just HTTP shape + admin gating; the
actual write logic is in
:meth:`magi.channels.telegram.adapter.TelegramAdapter.bind_im_id` /
``unbind_im_id`` / ``lookup_im_id`` — which writes both
``user_im_bindings`` (the canonical store) and the legacy
``Contact.telegram_id`` column (read-cache, kept for the
bot's inbound path).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from magi.bus import bootstrap
from magi.channels.webui.api.chat_sessions import _state_dir
from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException

router = APIRouter(tags=["telegram"])


class TGBindRequest(BaseModel):
    """Body for ``POST /api/telegram/bind``.

    ``uid`` is the row in ``contacts`` to bind to.
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

    contact = bootstrap(_state_dir()).contacts.get(payload.uid)
    if contact is None:
        raise MagiHTTPException(status_code=404, code="not_found.contact", detail=f"contact {payload.uid} not found")
    if contact.separated:
        raise MagiHTTPException(status_code=409, code="conflict.contact_separated", detail="restore the separated contact before binding Telegram")
    bootstrap(_state_dir()).contacts.bind_telegram(payload.uid, telegram_id_int)

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
        telegram_id_int = int(telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    # The dispatcher resolves the bound uid and the
    # adapter drops both the new and legacy rows.
    contact = bootstrap(_state_dir()).contacts.find_by_telegram_id(telegram_id_int)
    if contact is not None:
        bootstrap(_state_dir()).contacts.set_telegram_id(contact.id, None)
    return Response(status_code=204)


class TGBindStatus(BaseModel):
    telegram_id: str
    bound_uid: int | None
    bound_contact_name: str | None = None


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
    "unbind" confirmation with the contact name. Even
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
        telegram_id_int = int(telegram_id)
    except ValueError:
        raise MagiHTTPException(
            status_code=400,
            code="validation.telegram_id_invalid",
            detail="telegram_id must fit in an integer",
        )

    bound_uid = None
    bound_name = None
    contact = bootstrap(_state_dir()).contacts.find_by_telegram_id(telegram_id_int)
    if contact is not None:
        bound_uid = contact.id
        bound_name = contact.name
    return TGBindStatus(
        telegram_id=telegram_id,
        bound_uid=bound_uid,
        bound_contact_name=bound_name,
    )
