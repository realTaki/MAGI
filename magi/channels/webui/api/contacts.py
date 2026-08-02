"""Contact API — the unified contacts surface.

Serves two audiences:
  1. Knowledge → Contacts pane — ``GET /api/contacts?with_notes=true``
     returns contacts that have LLM-recorded notes.
  2. Admin CRUD — ``POST`` / ``GET/{id}`` / ``PATCH/{id}`` manage
     the contact directory (name, role, admin, TG binding).

LLM credentials are managed separately via ``/api/magic``
(the Magi row owns the provider + API key, not the Contact).

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

from magi.db import Contact, ContactNote, get_session
from magi.db.base import utcnow_naive
from magi.channels.webui.api.auth_gates import admin_gate, AdminGate
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.contacts")

router = APIRouter(tags=["contacts"])

_MAX_ROWS = 200
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100

# Valid ``role`` values — the relationship this contact
# has to MAGI. ``admin`` is intentionally NOT in this set:
# WebUI sign-in rights are carried by the separate
# ``admin`` boolean on the same row (see ``ContactOut.admin``
# and the ``/api/auth/me`` route). Splitting the two
# fields lets one contact be both ``role='assigned'`` (the
# person MAGI serves) AND ``admin=True`` (the operator).
_CONTACT_ROLES: tuple[str, ...] = ("assigned", "guest")


# -- helpers ----------------------------------------------------------------

def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")


# -- response / payload shapes ----------------------------------------------

class ContactOut(BaseModel):
    id: int
    name: str
    display_name: str | None = None
    role: str | None = None
    # WebUI sign-in rights — independent of ``role``. True
    # if the contact can authenticate to the operator
    # console (``/api/auth/me`` accepts the cookie; tasks
    # creator gate allows them). The split lets a contact
    # be ``role='assigned'`` (the person MAGI serves) AND
    # ``admin=True`` (the operator) at the same time.
    admin: bool = False
    separated_at: str | None = None
    telegram_id: int | None = None
    notes: str = ""
    notes_count: int = 0
    source: str = ""
    last_seen_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    # ``password_set`` is a boolean flag — the operator
    # never sees the hash, only whether the contact has
    # a credential row. Looks at the ``auth_credentials``
    # table; computed per-call so a row added via the
    # onboarding wizard appears immediately.
    password_set: bool = False
    # ``login_methods`` mirrors the AuthCredential table
    # + the bound IM. The frontend uses this to render
    # the Settings → Security card without a second
    # query round-trip.
    login_methods: list[str] = []


class ContactListOut(BaseModel):
    items: list[ContactOut]
    total: int
    page: int = 1
    page_size: int = 20
    total_pages: int = 1


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    role: str = Field(default="guest", max_length=16)
    # Defaults to ``False`` — a freshly-created contact is
    # not a WebUI operator until the operator explicitly
    # promotes them via ``PATCH /api/contacts/{id}`` with
    # ``{"admin": true}``. Pre-2024 schema conflated this
    # with ``role='admin'``; the split keeps the two
    # concerns independent.
    admin: bool = False
    telegram_id: int | None = None


class ContactUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = Field(default=None, max_length=16)
    # ``None`` (omitted from the PATCH body) means "leave
    # admin unchanged"; ``True`` / ``False`` flips the bit.
    # The ``model_fields_set`` check on the route side
    # distinguishes the two cases — critical because
    # ``False`` is a meaningful value (we want to be able
    # to revoke admin).
    admin: Optional[bool] = None
    telegram_id: Optional[int] = None
    separated: Optional[bool] = None


def _serialize(
    c: Contact,
    notes_count: int = 0,
    login_methods: list[str] | None = None,
) -> ContactOut:
    """Render a :class:`Contact` row to the wire shape.

    ``login_methods`` is computed by the caller (via
    :func:`_login_methods_for`) to avoid an N+1 query. A
    ``None`` value means "didn't compute — fall back to a
    single-row lookup", which is fine for the single
    ``GET /api/contacts/{id}`` path.
    """
    if login_methods is None:
        login_methods = _login_methods_for(c)
    return ContactOut(
        id=c.id,
        name=c.name,
        display_name=c.display_name,
        role=c.role,
        admin=bool(c.admin),
        separated_at=c.separated_at.isoformat() if c.separated_at else None,
        telegram_id=c.telegram_id,
        notes=c.notes,
        notes_count=notes_count,
        source=c.source,
        last_seen_at=_iso(c.last_seen_at),
        created_at=_iso(c.created_at),
        updated_at=_iso(c.updated_at),
        password_set="password" in login_methods,
        login_methods=login_methods,
    )


def _login_methods_for(contact: Contact) -> list[str]:
    """Compute the login methods for a single contact.

    Mirrors the same logic the auth endpoints use, kept
    inline so the contact route doesn't pay a round-trip
    through the auth module. Side-effect free.
    """
    methods: list[str] = []
    if contact.telegram_id is not None:
        methods.append("tg_code")
    # password_set is queried separately by the bulk
    # helper below — we let the caller pass the
    # precomputed set when batching a list response.
    return methods


def _bulk_login_methods(
    session: Session,
    contacts: list[Contact],
) -> dict[int, list[str]]:
    """Batch-fetch the password-set flag for a list of contacts.

    Returns ``{uid: methods}``. The tg_code leg is
    computed from the already-loaded contact row.
    """
    from magi.db.models_auth_credential import AuthCredential

    if not contacts:
        return {}
    uids = [c.id for c in contacts]
    password_uids: set[int] = set(
        session.scalars(
            select(AuthCredential.uid).where(
                AuthCredential.uid.in_(uids),
                AuthCredential.kind == "password",
            )
        ).all()
    )
    out: dict[int, list[str]] = {}
    for c in contacts:
        methods: list[str] = []
        if c.telegram_id is not None:
            methods.append("tg_code")
        if c.id in password_uids:
            methods.append("password")
        out[c.id] = methods
    return out


def _single_login_methods(
    session: Session,
    contact: Contact,
) -> list[str]:
    """Single-row helper for the by-id endpoints."""
    return _bulk_login_methods(session, [contact]).get(contact.id, [])


# -- routes -----------------------------------------------------------------

@router.get("/contacts", response_model=ContactListOut)
def list_contacts(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    with_notes: bool = False,
    separated: bool = False,
    include_separated: bool = False,
    role: str | None = None,
    admin: Optional[bool] = None,
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
        # Contacts that have at least one note in ``contact_notes``.
        from magi.db.models_contact import ContactNote
        note_ids = session.scalars(
            select(ContactNote.contact_id).distinct()
        ).all()
        base = base.where(Contact.id.in_(note_ids) if note_ids else False)
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

    if admin is not None and not with_notes:
        # ``admin=true`` ↔ WebUI operators. The Settings →
        # WebUI access card queries this filter to render
        # the admin list. Independent of ``role`` — see the
        # notes on ``ContactOut.admin``.
        base = base.where(Contact.admin == (1 if admin else 0))

    if with_notes:
        base = base.order_by(Contact.last_seen_at.desc()).limit(_MAX_ROWS)
        rows = session.scalars(base).all()
        # Preload notes counts in one query
        from magi.db.models_contact import ContactNote
        note_counts: dict[int, int] = {}
        if rows:
            cids = [r.id for r in rows]
            counts = session.execute(
                select(ContactNote.contact_id, func.count())
                .where(ContactNote.contact_id.in_(cids))
                .group_by(ContactNote.contact_id)
            ).all()
            note_counts = {c: int(n) for c, n in counts}
        login_methods = _bulk_login_methods(session, rows)
        return ContactListOut(
            items=[
                _serialize(
                    r,
                    notes_count=note_counts.get(r.id, 0),
                    login_methods=login_methods.get(r.id, []),
                )
                for r in rows
            ],
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
    login_methods = _bulk_login_methods(session, rows)
    return ContactListOut(
        items=[_serialize(r, login_methods=login_methods.get(r.id, [])) for r in rows],
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
    if payload.role == "assigned" and session.scalar(
        select(Contact.id).where(Contact.role == "assigned", Contact.separated_at.is_(None))
    ) is not None:
        raise MagiHTTPException(
            status_code=409,
            code="conflict.assigned_user_exists",
            detail="This MAGI already has an assigned user",
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
        role=payload.role,
        admin=payload.admin,
        telegram_id=payload.telegram_id,
    )
    session.add(contact)
    session.commit()
    session.refresh(contact)

    # Preset seed hook — fires only when the contact was
    # *created* as ``assigned`` from the start. The
    # helper is idempotent (skips per-(uid, preset_id)
    # pairs that already have a row), so a repeat
    # ``POST /api/contacts`` with the same name is
    # still 409 before we get here; this branch only
    # runs for the freshly-inserted contact.
    #
    # Wrapped in try/except so a preset-seeding failure
    # doesn't roll back the contact creation — the
    # contact row is more valuable than the preset rows.
    # The operator can re-trigger by editing the contact
    # (role stays assigned; an explicit
    # assigned→assigned PATCH below would also re-run
    # the helper, but the per-preset existence check
    # would short-circuit since the rows are already
    # there).
    if contact.role == "assigned":
        try:
            from magi.proactive.task_presets import seed_presets_for_contact
            seed_presets_for_contact(session, contact.id)
            session.commit()
        except Exception as exc:
            logger.warning(
                "preset seeding failed for newly-created contact %d: %s",
                contact.id, exc,
            )

    return _serialize(contact, login_methods=_single_login_methods(session, contact))


# -- notes sub-resource ---------------------------------------------------

class NoteOut(BaseModel):
    id: int
    contact_id: int
    note: str
    source: str
    created_at: str
    updated_at: str


class NoteListOut(BaseModel):
    items: list[NoteOut]
    total: int


@router.get("/contacts/{contact_id}/notes", response_model=NoteListOut)
def list_contact_notes(
    contact_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> NoteListOut:
    from magi.db.models_contact import Contact, ContactNote
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )
    rows = session.scalars(
        select(ContactNote)
        .where(ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc())
    ).all()
    items = [
        NoteOut(
            id=r.id,
            contact_id=r.contact_id,
            note=r.note,
            source=r.source,
            created_at=r.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=r.updated_at.isoformat().replace("+00:00", "Z"),
        )
        for r in rows
    ]
    return NoteListOut(items=items, total=len(items))


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
    return _serialize(contact, login_methods=_single_login_methods(session, contact))


# -- notes sub-resource ---------------------------------------------------

class NoteOut(BaseModel):
    id: int
    contact_id: int
    note: str
    source: str
    created_at: str
    updated_at: str


class NoteListOut(BaseModel):
    items: list[NoteOut]
    total: int


@router.get("/contacts/{contact_id}/notes", response_model=NoteListOut)
def list_contact_notes(
    contact_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> NoteListOut:
    from magi.db.models_contact import Contact, ContactNote
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )
    rows = session.scalars(
        select(ContactNote)
        .where(ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc())
    ).all()
    items = [
        NoteOut(
            id=r.id,
            contact_id=r.contact_id,
            note=r.note,
            source=r.source,
            created_at=r.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=r.updated_at.isoformat().replace("+00:00", "Z"),
        )
        for r in rows
    ]
    return NoteListOut(items=items, total=len(items))


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

    # Set inside the role branch; read after the commit
    # to decide whether to fire the preset-seed hook.
    # Initialised to False so a PATCH that doesn't touch
    # ``role`` is a clean no-op.
    newly_assigned = False

    if "name" in payload.model_fields_set and payload.name:
        contact.name = payload.name.strip()

    if "display_name" in payload.model_fields_set:
        contact.display_name = payload.display_name

    if "separated" in payload.model_fields_set:
        contact.separated_at = utcnow_naive() if payload.separated else None

    if "role" in payload.model_fields_set and payload.role is not None:
        if payload.role not in _CONTACT_ROLES:
            raise MagiHTTPException(
                status_code=400, code="validation.role_unknown",
                detail=f"Unknown role {payload.role!r}",
            )
        # Capture the *prior* role so the post-commit
        # hook can detect a transition INTO assigned (vs.
        # an idempotent assigned→assigned PATCH that
        # shouldn't trigger a fresh seed round).
        prev_role = contact.role
        if payload.role == "assigned" and prev_role != "assigned" and session.scalar(
            select(Contact.id).where(
                Contact.role == "assigned",
                Contact.separated_at.is_(None),
                Contact.id != contact.id,
            )
        ) is not None:
            raise MagiHTTPException(
                status_code=409,
                code="conflict.assigned_user_exists",
                detail="This MAGI already has an assigned user",
            )
        contact.role = payload.role
        # Tag the local variable for the post-commit
        # branch. We need this outside the ``if`` so it
        # survives the conditional execution.
        newly_assigned = (
            payload.role == "assigned" and prev_role != "assigned"
        )

    # ``admin`` toggle — independent of ``role`` (the role
    # transition above has its own seed-hook trigger; the
    # admin bit doesn't affect seeding).
    if "admin" in payload.model_fields_set and payload.admin is not None:
        contact.admin = bool(payload.admin)

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

    # Preset seed hook — fires only on a TRUE transition
    # into ``assigned``. assigned→admin→assigned would
    # also qualify (the prev_role at this commit is
    # ``admin``), which matches the intent: "this
    # contact just became assigned; seed them". The
    # helper's per-(uid, preset_id) existence check
    # short-circuits when rows already exist, so a
    # double-seed is a no-op rather than a duplicate.
    if newly_assigned:
        try:
            from magi.proactive.task_presets import seed_presets_for_contact
            seed_presets_for_contact(session, contact.id)
            session.commit()
        except Exception as exc:
            logger.warning(
                "preset seeding failed for contact %d (role → assigned): %s",
                contact.id, exc,
            )

    return _serialize(contact, login_methods=_single_login_methods(session, contact))


# -- notes sub-resource ---------------------------------------------------

class NoteOut(BaseModel):
    id: int
    contact_id: int
    note: str
    source: str
    created_at: str
    updated_at: str


class NoteListOut(BaseModel):
    items: list[NoteOut]
    total: int


@router.get("/contacts/{contact_id}/notes", response_model=NoteListOut)
def list_contact_notes(
    contact_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> NoteListOut:
    from magi.db.models_contact import Contact, ContactNote
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )
    rows = session.scalars(
        select(ContactNote)
        .where(ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc())
    ).all()
    items = [
        NoteOut(
            id=r.id,
            contact_id=r.contact_id,
            note=r.note,
            source=r.source,
            created_at=r.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=r.updated_at.isoformat().replace("+00:00", "Z"),
        )
        for r in rows
    ]
    return NoteListOut(items=items, total=len(items))


# -- notes sub-resource ---------------------------------------------------

class NoteOut(BaseModel):
    id: int
    contact_id: int
    note: str
    source: str
    created_at: str
    updated_at: str


class NoteListOut(BaseModel):
    items: list[NoteOut]
    total: int


@router.get("/contacts/{contact_id}/notes", response_model=NoteListOut)
def list_contact_notes(
    contact_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> NoteListOut:
    from magi.db.models_contact import Contact, ContactNote
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )
    rows = session.scalars(
        select(ContactNote)
        .where(ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc())
    ).all()
    items = [
        NoteOut(
            id=r.id,
            contact_id=r.contact_id,
            note=r.note,
            source=r.source,
            created_at=r.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=r.updated_at.isoformat().replace("+00:00", "Z"),
        )
        for r in rows
    ]
    return NoteListOut(items=items, total=len(items))
