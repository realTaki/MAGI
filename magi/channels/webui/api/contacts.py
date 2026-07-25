"""Contact API — the unified contacts surface.

Serves two audiences:
  1. Knowledge → Contacts pane — ``GET /api/contacts?with_notes=true``
     returns contacts that have LLM-recorded notes.
  2. Admin CRUD — ``POST`` / ``GET/{id}`` / ``PATCH/{id}`` manage
     the contact directory (name, role, provider, api_key, TG).

The ``admin_gate`` is re-exported from :mod:`.auth_gates` so
other routers can import it from here if needed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from magi.agent.db import Contact, get_session
from magi.agent.db.base import utcnow_naive
from magi.channels.webui.api.auth_gates import admin_gate, AdminGate
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.contacts")

router = APIRouter(tags=["contacts"])

_MAX_ROWS = 200
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100

_CONTACT_ROLES: tuple[str, ...] = ("admin", "assigned", "contact", "guest")


# -- helpers ----------------------------------------------------------------

def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")


def _mask_key(raw: str | None) -> tuple[bool, str | None]:
    if not raw:
        return False, None
    return True, (raw[-4:] if len(raw) >= 4 else raw)


# -- response / payload shapes ----------------------------------------------

class ContactOut(BaseModel):
    id: int
    name: str
    display_name: str | None = None
    role: str | None = None
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    separated_at: str | None = None
    telegram_id: int | None = None
    notes: str = ""
    source: str = ""
    last_seen_at: str = ""
    created_at: str = ""
    updated_at: str = ""


class ContactListOut(BaseModel):
    items: list[ContactOut]
    total: int
    page: int = 1
    page_size: int = 20
    total_pages: int = 1


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    role: str = Field(default="contact", max_length=16)
    telegram_id: int | None = None


class ContactUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    provider: Optional[str] = Field(default=None, max_length=32)
    api_key: Optional[str] = Field(default=None, max_length=512)
    name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = Field(default=None, max_length=16)
    telegram_id: Optional[int] = None
    separated: Optional[bool] = None


def _serialize(c: Contact) -> ContactOut:
    is_set, last4 = _mask_key(c.api_key)
    return ContactOut(
        id=c.id,
        name=c.name,
        display_name=c.display_name,
        role=c.role,
        provider=c.provider,
        api_key_set=is_set,
        api_key_last4=last4,
        separated_at=c.separated_at.isoformat() if c.separated_at else None,
        telegram_id=c.telegram_id,
        notes=c.notes,
        source=c.source,
        last_seen_at=_iso(c.last_seen_at),
        created_at=_iso(c.created_at),
        updated_at=_iso(c.updated_at),
    )


# -- routes -----------------------------------------------------------------

@router.get("/contacts", response_model=ContactListOut)
def list_contacts(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    with_notes: bool = False,
    separated: bool = False,
    include_separated: bool = False,
    role: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> ContactListOut:
    """List contacts.

    ``with_notes=true`` → Knowledge pane: only contacts
    with non-empty notes (LLM-recorded directory).

    Without ``with_notes`` → Admin CRUD view with optional
    role / separated filters + pagination.
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = _PAGE_SIZE_DEFAULT
    if page_size > _PAGE_SIZE_MAX:
        page_size = _PAGE_SIZE_MAX

    base = select(Contact)

    if with_notes:
        base = base.where(Contact.notes != "")
    elif separated:
        base = base.where(Contact.separated_at.is_not(None))
    elif not include_separated:
        base = base.where(Contact.separated_at.is_(None))

    if role is not None and not with_notes:
        if role not in _CONTACT_ROLES:
            raise MagiHTTPException(
                status_code=400,
                code="validation.role_unknown",
                detail=f"Unknown role {role!r}. Valid: {', '.join(_CONTACT_ROLES)}",
            )
        base = base.where(Contact.role == role)

    if with_notes:
        base = base.order_by(Contact.last_seen_at.desc()).limit(_MAX_ROWS)
        rows = session.scalars(base).all()
        return ContactListOut(
            items=[_serialize(r) for r in rows],
            total=len(rows),
            page=1,
            page_size=len(rows),
            total_pages=1,
        )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    page_q = (
        base.order_by(Contact.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = session.scalars(page_q).all()
    return ContactListOut(
        items=[_serialize(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(
    payload: ContactCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> ContactOut:
    name = payload.name.strip()
    if not name:
        raise MagiHTTPException(
            status_code=400, code="validation.name_required",
            detail="name must not be empty",
        )
    if session.scalar(select(Contact).where(Contact.name == name)) is not None:
        raise MagiHTTPException(
            status_code=409, code="conflict.contact_name_exists",
            detail=f"contact {name!r} already exists",
        )
    if payload.role not in _CONTACT_ROLES:
        raise MagiHTTPException(
            status_code=400, code="validation.role_unknown",
            detail=f"Unknown role {payload.role!r}. Valid: {', '.join(_CONTACT_ROLES)}",
        )
    if payload.telegram_id is not None and session.scalar(
        select(Contact).where(Contact.telegram_id == payload.telegram_id)
    ) is not None:
        raise MagiHTTPException(
            status_code=409, code="conflict.telegram_id_already_bound",
            detail=f"telegram_id {payload.telegram_id} is already bound",
        )
    contact = Contact(
        name=name,
        display_name=payload.display_name,
        provider=payload.provider,
        api_key=payload.api_key,
        role=payload.role,
        telegram_id=payload.telegram_id,
    )
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return _serialize(contact)


@router.get("/contacts/{contact_id}", response_model=ContactOut)
def get_contact(
    contact_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> ContactOut:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )
    return _serialize(contact)


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> ContactOut:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )

    if "name" in payload.model_fields_set and payload.name:
        contact.name = payload.name.strip()

    if "display_name" in payload.model_fields_set:
        contact.display_name = payload.display_name

    if "provider" in payload.model_fields_set:
        contact.provider = payload.provider

    if "api_key" in payload.model_fields_set:
        contact.api_key = payload.api_key if payload.api_key else None

    if "separated" in payload.model_fields_set:
        contact.separated_at = utcnow_naive() if payload.separated else None

    if "role" in payload.model_fields_set and payload.role is not None:
        if payload.role not in _CONTACT_ROLES:
            raise MagiHTTPException(
                status_code=400, code="validation.role_unknown",
                detail=f"Unknown role {payload.role!r}",
            )
        contact.role = payload.role

    if "telegram_id" in payload.model_fields_set:
        new_tg = payload.telegram_id
        if new_tg is not None:
            existing = session.scalar(
                select(Contact).where(Contact.telegram_id == new_tg)
            )
            if existing is not None and existing.id != contact.id:
                raise MagiHTTPException(
                    status_code=409, code="conflict.telegram_id_already_bound",
                    detail=f"telegram_id {new_tg} is already bound",
                )
        contact.telegram_id = new_tg

    session.commit()
    session.refresh(contact)
    return _serialize(contact)
