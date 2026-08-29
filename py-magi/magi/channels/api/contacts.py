"""Contact API — the unified contacts surface.

Serves two audiences:
  1. Knowledge → Contacts pane — ``GET /api/contacts?with_notes=true``
     returns contacts that have LLM-recorded notes.
  2. Admin CRUD — ``POST`` / ``GET/{id}`` / ``PATCH/{id}`` manage
     the contact directory (name, role, TG binding).

LLM credentials are managed separately via ``/api/magi``
(the Magi row owns the provider + API key, not the Contact).

The ``admin_gate`` is re-exported from :mod:`.auth_gates` so
other routers can import it from here if needed.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.old_bus import Bus
from magi.old_bus.firmwares.books.local import Contact, Role
from magi.old_bus.firmwares.jobs.seedPresetTasksJob import SeedPresetTaskJob
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.contacts")

router = APIRouter(tags=["contacts"])

_MAX_ROWS = 200
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100

#: Closed set of accepted ``role`` query values. Frozen so it can be
#: reused as a lookup table in :func:`list_contacts` without anyone
#: mutating it by accident.
_CONTACT_ROLES: frozenset[str] = frozenset(r.value for r in Role)

# Valid local roles. MAGIS administrator authority is deliberately absent:
# a Contact may only be a local served user or guest — see ``Role`` for the
# canonical enum. ``payload.role`` is wire-shape ``str`` (Pydantic); the
# ``in Role`` / ``== Role.ASSIGNED`` checks below exploit ``StrEnum``'s
# ``str`` equality so the boundary stays a passthrough.
_VALID_LOCAL_ROLES: tuple[str, ...] = tuple(r.value for r in Role)


# -- response / payload shapes ----------------------------------------------


class ContactOut(BaseModel):
    id: int
    name: str
    display_name: str | None = None
    role: Role | None = None
    tgid: int | None = None
    notes: str = ""
    notes_count: int = 0
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ContactListOut(BaseModel):
    items: list[ContactOut]
    total: int
    page: int = 1
    page_size: int = 20
    total_pages: int = 1


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    role: Role = Role.GUEST
    tgid: int | None = None


class ContactUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    role: Role | None = None
    tgid: int | None = None


def _serialize(view: Any, notes_count: int = 0) -> ContactOut:
    """Render a :class:`Any` to the wire shape.

    Local contact records do not expose MAGIS admin authentication state.
    """
    return ContactOut(
        id=view.id,
        name=view.name,
        display_name=view.display_name,
        role=view.role,
        tgid=view.tgid,
        notes="",
        notes_count=notes_count,
        last_seen_at=view.last_seen_at,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _two_factor_is_enabled(request: Request, bus: Bus) -> bool:
    """Read the caller's auth posture only on the two user-creation writes.

    This is deliberately not a general API gate: a bootstrap admin can use
    every other normal feature without IM verification.
    """
    from magi.channels.api.proxy_auth import verified_proxy_scope

    scope = verified_proxy_scope(bus, request)
    if scope is not None:
        is_admin, _assigned, two_factor, _admin_id = scope
        return is_admin and two_factor
    from magi.channels.api.auth import resolve_session

    session = resolve_session(bus, request.cookies.get("magi_session"))
    return bool(session and session.get("admin") and session.get("two_factor"))


# -- routes -----------------------------------------------------------------


@router.get("/contacts", response_model=ContactListOut)
def list_contacts(
    _admin: AdminGate,
    bus: BusDep,
    with_notes: bool = False,
    role: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> ContactListOut:
    """List contacts.

    ``with_notes=true`` → Knowledge pane: only contacts
    with non-empty notes (LLM-recorded directory).

    Without ``with_notes`` → Admin CRUD view with optional
    role filter + pagination.
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = _PAGE_SIZE_DEFAULT
    if page_size > _PAGE_SIZE_MAX:
        page_size = _PAGE_SIZE_MAX

    if role is not None and not with_notes:
        if role not in _CONTACT_ROLES:
            raise MagiHTTPException(
                status_code=400,
                code="validation.role_unknown",
                detail=f"Unknown role {role!r}. Valid: {', '.join(_CONTACT_ROLES)}",
            )

    if with_notes:
        views = bus.contacts_book.list_all()[:_MAX_ROWS]
        contact_ids = [v.id for v in views]
        counts = {
            contact_id: len(bus.contact_notes_book.list_for_contact(contact_id=contact_id))
            for contact_id in contact_ids
        }
        views = [view for view in views if counts[view.id] > 0]
        return ContactListOut(
            items=[
                _serialize(v, notes_count=counts.get(v.id, 0))
                for v in views
            ],
            total=len(views),
            page=1,
            page_size=len(views),
            total_pages=1,
        )

    rows = [
        view
        for view in bus.contacts_book.list_all()
        if role is None or view.role == role
    ]
    total = len(rows)
    rows = rows[(page - 1) * page_size : page * page_size]
    total_pages = max(1, (total + page_size - 1) // page_size)
    return ContactListOut(
        items=[_serialize(v) for v in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(
    payload: ContactCreate,
    _admin: AdminGate,
    bus: BusDep,
    request: Request,
) -> ContactOut:
    name = payload.name.strip()
    if not name:
        raise MagiHTTPException(
            status_code=400,
            code="validation.name_required",
            detail="name must not be empty",
        )
    if payload.role not in _VALID_LOCAL_ROLES:
        raise MagiHTTPException(
            status_code=400,
            code="validation.role_unknown",
            detail=f"Unknown role {payload.role!r}. Valid: {', '.join(_VALID_LOCAL_ROLES)}",
        )
    if payload.role == Role.ASSIGNED and not _two_factor_is_enabled(request, bus):
        raise MagiHTTPException(
            status_code=403,
            code="auth.two_factor_required_for_user_creation",
            detail="Enable IM two-factor verification before creating an assigned user",
        )
    if payload.role == Role.ASSIGNED and any(
        view.role == Role.ASSIGNED for view in bus.contacts_book.list_all()
    ):
        raise MagiHTTPException(
            status_code=409,
            code="conflict.assigned_user_exists",
            detail="This MAGI already has an assigned user",
        )
    if payload.tgid is not None and bus.contacts_book.get_by_telegram(
        tgid=payload.tgid
    ):
        raise MagiHTTPException(
            status_code=409,
            code="conflict.tgid_already_bound",
            detail=f"tgid {payload.tgid} is already bound",
        )
    record_id = bus.contacts_book.add(Contact(
        name=name,
        display_name=payload.display_name,
        role=payload.role,
        tgid=payload.tgid,
    ))
    view = bus.contacts_book.get(record_id)
    if view is None:
        raise RuntimeError(f"contact row {record_id} disappeared after insert")

    # Preset seed hook — fires only when the contact was
    # *created* as ``assigned`` from the start. The
    # helper is idempotent (skips per-(contact_id, preset_id)
    # pairs that already have a row), so a repeat
    # ``POST /api/contacts`` with the same name is
    # still 409 before we get here; this branch only
    # runs for the freshly-inserted contact.
    #
    # Wrapped in try/except so a preset-seeding failure
    # doesn't roll back the contact creation — the
    # contact row is more valuable than the preset rows.
    #
    if view.role == Role.ASSIGNED:
        # Publish **one job per preset** so the worker logs each
        # insertion / skip independently. A bulk job that reports
        # ``inserted=3, skipped=2`` forces post-mortem log-diving to
        # figure out which preset failed; one-job-per-preset makes
        # the failure surface obvious from the JobBoard's claim log.
        try:
            for prompt_key in bus.prompt_book.list():
                if not prompt_key.startswith("proactive/"):
                    continue
                preset_key = prompt_key.removeprefix("proactive/")
                bus.seed_preset_task_job_board.publish(
                    SeedPresetTaskJob(
                        contact_id=view.id,
                        preset_key=preset_key,
                    ),
                )
        except Exception as exc:
            logger.warning(
                "preset seeding dispatch failed for newly-created contact %d: %s",
                view.id,
                exc,
            )

    return _serialize(view)


# -- notes sub-resource ---------------------------------------------------


class NoteOut(BaseModel):
    id: int
    contact_id: int
    note: str
    created_at: datetime
    updated_at: datetime


class NoteListOut(BaseModel):
    items: list[NoteOut]
    total: int


def _note_view_out(view: Any) -> NoteOut:
    return NoteOut(
        id=view.id,
        contact_id=view.contact_id,
        note=view.note,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get("/contacts/{contact_id}/notes", response_model=NoteListOut)
def list_contact_notes(
    contact_id: int,
    _admin: AdminGate,
    bus: BusDep,
) -> NoteListOut:
    contact = bus.contacts_book.get(contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.contact",
            detail="contact not found",
        )
    notes = bus.contact_notes_book.list_for_contact(contact_id=contact_id)
    items = [_note_view_out(n) for n in notes]
    return NoteListOut(items=items, total=len(items))


@router.get("/contacts/{contact_id}", response_model=ContactOut)
def get_contact(
    contact_id: int,
    _admin: AdminGate,
    bus: BusDep,
) -> ContactOut:
    view = bus.contacts_book.get(contact_id)
    if view is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.contact",
            detail="contact not found",
        )
    return _serialize(view)


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    _admin: AdminGate,
    bus: BusDep,
    request: Request,
) -> ContactOut:
    existing = bus.contacts_book.get(contact_id)
    if existing is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.contact",
            detail="contact not found",
        )

    # Set inside the role branch; read after the commit
    # to decide whether to fire the preset-seed hook.
    # Initialised to False so a PATCH that doesn't touch
    # ``role`` is a clean no-op.
    newly_assigned = False

    new_name: str | None = None
    if "name" in payload.model_fields_set and payload.name:
        new_name = payload.name.strip()

    new_display_name: str | None = None
    if "display_name" in payload.model_fields_set:
        new_display_name = payload.display_name

    new_role: str | None = None
    if "role" in payload.model_fields_set and payload.role is not None:
        if payload.role not in _VALID_LOCAL_ROLES:
            raise MagiHTTPException(
                status_code=400,
                code="validation.role_unknown",
                detail=f"Unknown role {payload.role!r}",
            )
        # Capture the *prior* role so the post-commit
        # hook can detect a transition INTO assigned (vs.
        # an idempotent assigned→assigned PATCH that
        # shouldn't trigger a fresh seed round).
        prev_role = existing.role
        if (
            payload.role == Role.ASSIGNED
            and prev_role != Role.ASSIGNED
            and not _two_factor_is_enabled(request, bus)
        ):
            raise MagiHTTPException(
                status_code=403,
                code="auth.two_factor_required_for_user_creation",
                detail="Enable IM two-factor verification before creating an assigned user",
            )
        if (
            payload.role == Role.ASSIGNED
            and prev_role != Role.ASSIGNED
            and any(
                view.role == Role.ASSIGNED and view.id != contact_id
                for view in bus.contacts_book.list_all()
            )
        ):
            raise MagiHTTPException(
                status_code=409,
                code="conflict.assigned_user_exists",
                detail="This MAGI already has an assigned user",
            )
        new_role = payload.role
        # Tag the local variable for the post-commit
        # branch. We need this outside the ``if`` so it
        # survives the conditional execution.
        newly_assigned = payload.role == Role.ASSIGNED and prev_role != Role.ASSIGNED

    new_tgid: int | None = None
    if "tgid" in payload.model_fields_set:
        new_tg = payload.tgid
        bound = (
            bus.contacts_book.get_by_telegram(tgid=new_tg) if new_tg is not None else None
        )
        if bound is not None and bound.id != contact_id:
            raise MagiHTTPException(
                status_code=409,
                code="conflict.tgid_already_bound",
                detail=f"tgid {new_tg} is already bound",
            )
        new_tgid = new_tg

    current = bus.contacts_book.get(contact_id)
    if current is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.contact",
            detail="contact not found",
        )
    candidate = replace(
        current,
        name=new_name if new_name is not None else current.name,
        display_name=(new_display_name if "display_name" in payload.model_fields_set else current.display_name),
        role=new_role if new_role is not None else current.role,
        tgid=(new_tgid if "tgid" in payload.model_fields_set else current.tgid),
    )
    bus.contacts_book.update(candidate)
    view = bus.contacts_book.get(contact_id)
    assert view is not None

    # Preset seed hook — fires only on a TRUE transition
    # into ``assigned``. assigned→admin→assigned would
    # also qualify (the prev_role at this commit is
    # ``admin``), which matches the intent: "this
    # contact just became assigned; seed them". The
    # helper's per-(contact_id, preset_id) existence check
    # short-circuits when rows already exist, so a
    # double-seed is a no-op rather than a duplicate.
    #
    # TODO(proactive-refactor): currently dispatches one job per preset to
    # bus.seed_preset_task_job_board; ProactiveWorker is the async consumer.
    # One-job-per-preset means each preset's failure mode shows up in the
    # JobBoard claim log without post-mortem log-diving.
    if newly_assigned:
        try:
            for prompt_key in bus.prompt_book.list():
                if not prompt_key.startswith("proactive/"):
                    continue
                preset_key = prompt_key.removeprefix("proactive/")
                bus.seed_preset_task_job_board.publish(
                    SeedPresetTaskJob(
                        contact_id=view.id,
                        preset_key=preset_key,
                    ),
                )
        except Exception as exc:
            logger.warning(
                "preset seeding dispatch failed for contact %d (role → assigned): %s",
                view.id,
                exc,
            )

    return _serialize(view)
