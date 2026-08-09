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
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.bus.guild.seedPresetTasksJob import SeedPresetTasksJob
from magi.channels.api.auth_gates import admin_gate, AdminGate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

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

def _iso(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
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
    telegram_id: int | None = None
    notes: str = ""
    notes_count: int = 0
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


def _serialize(
    view: Any,
    notes_count: int = 0,
    login_methods: list[str] | None = None,
) -> ContactOut:
    """Render a :class:`Any` to the wire shape.

    ``login_methods`` is computed by the caller (via
    :func:`_login_methods_for`) to avoid an N+1 query. A
    ``None`` value means "didn't compute — fall back to a
    single-row lookup", which is fine for the single
    ``GET /api/contacts/{id}`` path.
    """
    if login_methods is None:
        login_methods = _login_methods_for(view)
    return ContactOut(
        id=view.id,
        name=view.name,
        display_name=view.display_name,
        role=view.role,
        admin=view.admin,
        telegram_id=view.telegram_id,
        notes="",
        notes_count=notes_count,
        last_seen_at=view.last_seen_at,
        created_at=view.created_at,
        updated_at=view.updated_at,
        password_set="password" in login_methods,
        login_methods=login_methods,
    )


def _login_methods_for(view: Any) -> list[str]:
    """Compute the login methods for a single contact.

    Mirrors the same logic the auth endpoints use, kept
    inline so the contact route doesn't pay a round-trip
    through the auth module. Side-effect free.
    """
    methods: list[str] = []
    if view.telegram_id is not None:
        methods.append("tg_code")
    # password_set is queried separately by the bulk
    # helper below — we let the caller pass the
    # precomputed set when batching a list response.
    return methods


def _bulk_login_methods(
    bus: Bus,
    views: list[Any],
) -> dict[int, list[str]]:
    """Batch-fetch the password-set flag for a list of contacts.

    Returns ``{uid: methods}``. The tg_code leg is
    computed from the already-loaded contact view.
    """
    if not views:
        return {}
    uids = [v.id for v in views]
    password_uids = {
        uid for uid in uids
        if bus.auth_credentials_book is not None
        and bus.auth_credentials_book.find(uid=uid, kind="password") is not None
    }
    out: dict[int, list[str]] = {}
    for v in views:
        methods: list[str] = []
        if v.telegram_id is not None:
            methods.append("tg_code")
        if v.id in password_uids:
            methods.append("password")
        out[v.id] = methods
    return out


def _single_login_methods(
    bus: Bus,
    view: Any,
) -> list[str]:
    """Single-row helper for the by-id endpoints."""
    return _bulk_login_methods(bus, [view]).get(view.id, [])


# -- routes -----------------------------------------------------------------

@router.get("/contacts", response_model=ContactListOut)
def list_contacts(
    _admin: AdminGate,
    bus: BusDep,
    with_notes: bool = False,
    role: str | None = None,
    admin: Optional[bool] = None,
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

    if admin is not None and not with_notes:
        # ``admin=true`` ↔ WebUI operators. The Settings →
        # WebUI access card queries this filter to render
        # the admin list. Independent of ``role`` — see the
        # notes on ``ContactOut.admin``.
        pass

    if with_notes:
        views = bus.contacts_book.list_all()[:_MAX_ROWS]
        uids = [v.id for v in views]
        counts = {
            uid: len(bus.contact_notes_book.list_for_contact(contact_id=uid))
            for uid in uids
        }
        views = [view for view in views if counts[view.id] > 0]
        login_methods = _bulk_login_methods(bus, views)
        return ContactListOut(
            items=[
                _serialize(
                    v,
                    notes_count=counts.get(v.id, 0),
                    login_methods=login_methods.get(v.id, []),
                )
                for v in views
            ],
            total=len(views),
            page=1,
            page_size=len(views),
            total_pages=1,
        )

    rows = [
        view for view in bus.contacts_book.list_all()
        if (role is None or view.role == role)
        and (admin is None or view.admin == admin)
    ]
    total = len(rows)
    rows = rows[(page - 1) * page_size: page * page_size]
    login_methods = _bulk_login_methods(bus, rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return ContactListOut(
        items=[_serialize(v, login_methods=login_methods.get(v.id, [])) for v in rows],
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
) -> ContactOut:
    name = payload.name.strip()
    if not name:
        raise MagiHTTPException(
            status_code=400, code="validation.name_required",
            detail="name must not be empty",
        )
    if any(view.name == name for view in bus.contacts_book.list_all()):
        raise MagiHTTPException(
            status_code=409, code="conflict.contact_name_exists",
            detail=f"contact {name!r} already exists",
        )
    if payload.role not in _CONTACT_ROLES:
        raise MagiHTTPException(
            status_code=400, code="validation.role_unknown",
            detail=f"Unknown role {payload.role!r}. Valid: {', '.join(_CONTACT_ROLES)}",
        )
    if payload.role == "assigned" and any(view.role == "assigned" for view in bus.contacts_book.list_all()):
        raise MagiHTTPException(
            status_code=409,
            code="conflict.assigned_user_exists",
            detail="This MAGI already has an assigned user",
        )
    if payload.telegram_id is not None and bus.contacts_book.get_by_telegram(telegram_id=payload.telegram_id):
        raise MagiHTTPException(
            status_code=409, code="conflict.telegram_id_already_bound",
            detail=f"telegram_id {payload.telegram_id} is already bound",
        )
    view = bus.contacts_book.add(
        name=name,
        display_name=payload.display_name,
        role=payload.role,
        admin=payload.admin,
        telegram_id=payload.telegram_id,
    )

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
    #
    if view.role == "assigned":
        try:
            bus.seed_preset_tasks_job_board.publish(
                SeedPresetTasksJob(contact_id=view.id, trigger="contact_created"),
            )
        except Exception as exc:
            logger.warning(
                "preset seeding failed for newly-created contact %d: %s",
                view.id, exc,
            )

    return _serialize(view, login_methods=_single_login_methods(bus, view))


# -- notes sub-resource ---------------------------------------------------

class NoteOut(BaseModel):
    id: int
    contact_id: int
    note: str
    created_at: str
    updated_at: str


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
    contact = bus.contacts_book.get(contact_id=contact_id)
    if contact is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
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
    view = bus.contacts_book.get(contact_id=contact_id)
    if view is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )
    return _serialize(view, login_methods=_single_login_methods(bus, view))


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    _admin: AdminGate,
    bus: BusDep,
) -> ContactOut:
    existing = bus.contacts_book.get(contact_id=contact_id)
    if existing is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )

    # Set inside the role branch; read after the commit
    # to decide whether to fire the preset-seed hook.
    # Initialised to False so a PATCH that doesn't touch
    # ``role`` is a clean no-op.
    newly_assigned = False

    new_name: Optional[str] = None
    if "name" in payload.model_fields_set and payload.name:
        new_name = payload.name.strip()

    new_display_name: Optional[str] = None
    if "display_name" in payload.model_fields_set:
        new_display_name = payload.display_name

    new_role: Optional[str] = None
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
        prev_role = existing.role
        if payload.role == "assigned" and prev_role != "assigned" and any(
            view.role == "assigned" and view.id != contact_id
            for view in bus.contacts_book.list_all()
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
        newly_assigned = (
            payload.role == "assigned" and prev_role != "assigned"
        )

    new_admin: Optional[bool] = None
    # ``admin`` toggle — independent of ``role`` (the role
    # transition above has its own seed-hook trigger; the
    # admin bit doesn't affect seeding).
    if "admin" in payload.model_fields_set and payload.admin is not None:
        new_admin = bool(payload.admin)

    new_telegram_id: Optional[int] = None
    if "telegram_id" in payload.model_fields_set:
        new_tg = payload.telegram_id
        bound = bus.contacts_book.get_by_telegram(telegram_id=new_tg) if new_tg is not None else None
        if bound is not None and bound.id != contact_id:
            raise MagiHTTPException(
                status_code=409, code="conflict.telegram_id_already_bound",
                detail=f"telegram_id {new_tg} is already bound",
            )
        new_telegram_id = new_tg

    view = bus.contacts_book.update(
        contact_id=contact_id,
        name=new_name,
        display_name=new_display_name,
        role=new_role,
        admin=new_admin,
        telegram_id=new_telegram_id,
        set_display_name="display_name" in payload.model_fields_set,
        set_telegram_id="telegram_id" in payload.model_fields_set,
    )
    if view is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.contact",
            detail="contact not found",
        )

    # Preset seed hook — fires only on a TRUE transition
    # into ``assigned``. assigned→admin→assigned would
    # also qualify (the prev_role at this commit is
    # ``admin``), which matches the intent: "this
    # contact just became assigned; seed them". The
    # helper's per-(uid, preset_id) existence check
    # short-circuits when rows already exist, so a
    # double-seed is a no-op rather than a duplicate.
    #
    # TODO(proactive-refactor): 改为发布 SeedPresetTasksJob 到
    # bus.seed_preset_tasks_job_board，由 ProactiveWorker
    # 异步消费。trigger 标记 "contact_promoted"。当前同步
    # 调用将在 Worker 就绪 + 验证稳定后移除。
    if newly_assigned:
        try:
            bus.seed_preset_tasks_job_board.publish(
                SeedPresetTasksJob(contact_id=view.id, trigger="contact_promoted"),
            )
        except Exception as exc:
            logger.warning(
                "preset seeding failed for contact %d (role → assigned): %s",
                view.id, exc,
            )

    return _serialize(view, login_methods=_single_login_methods(bus, view))
