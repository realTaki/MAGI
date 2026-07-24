"""Employee API — the directory / TG-binding / role surface.

Split out of the old ``departments`` router when the
``departments`` / ``Department`` concept was dropped (the
org shape is now ``MAGIC`` → ``Magi`` → ``User``, with no
dept sub-tree). The auth gate dependencies
(``admin_gate`` / ``admin_or_assigned_gate``) live here so
other routers (``soul``, ``tasks``, ``magis``, …) can reuse
them without re-implementing the cookie + role check.

All routes require the user to be signed in (the existing
``/api/auth/me`` check) and to be an admin (an ``Employee``
row with ``role='admin'``) — both checks run in the
``admin_gate`` dependency, keeping the auth gate in one place.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request, Response

from magi.channels.webui.api.errors import MagiHTTPException
from magi.agent.db.base import utcnow_naive
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from magi.agent.db import (
    Employee,
    get_session,
)

logger = logging.getLogger("magi.api.employees")

router = APIRouter(tags=["employees"])


# -- auth gate --------------------------------------------------------------

def _is_admin_uid(uid: int) -> bool:
    """True if the ``Employee`` row with the given id has role=admin.

    D.24: the ``magi_session`` cookie carries the
    ``uid`` (cross-channel identity), not a chat id.
    The admin allowlist is keyed by employee id; a future
    channel will resolve its own delivery address to the same
    employee id and re-use this same check.

    An ORM read failure (table not yet initialised, etc.) is a
    hard ``False`` — the gate fails closed rather than silently
    letting unauthenticated callers through. ``admin_gate`` is
    the only auth path; the chat endpoint and action_items
    endpoint both pre-check via this same gate.
    """
    from magi.agent.db import open_session

    try:
        with open_session() as session:
            emp = session.get(Employee, uid)
            if emp is not None and emp.role == "admin":
                return True
    except Exception:
        logger.exception("admin_gate: ORM read failed; denying access")

    return False


def admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is a super admin.

    D.24: reads the ``uid`` from the cookie and
    looks up the row's role directly. The auth router
    validates the same cookie in its ``/me`` handler, so by
    the time a request gets here the cookie is known to be a
    live session; this gate just re-checks the caller is
    still in the super-admins list (a stale cookie after an
    admin removal shouldn't sneak past).

    Returns the cookie's uid as a string for
    call-site convenience (the chat_sessions router casts it
    back to ``int``).
    """
    raw = request.cookies.get("magi_session")
    if not raw or not raw.isdigit():
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    uid = int(raw)
    if not _is_admin_uid(uid):
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    return raw


AdminGate = Annotated[str, Depends(admin_gate)]


def admin_or_assigned_gate(request: Request) -> str:
    """FastAPI dependency — ``admin`` or ``assigned`` employee.

    Read paths (GET) and write paths (PUT/POST) on
    ``/api/soul`` both gate through this; the soul editor is
    the first feature where ``assigned`` employees get a
    write surface, but they don't get full admin powers
    (employee CRUD, settings etc. stay admin-only).

    D.24: cookie carries ``Employee.id`` (an int), not the
    legacy telegram_id.
    """
    from magi.agent.db import open_session

    raw = request.cookies.get("magi_session") or ""
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail=(
                "SOUL.md editing requires admin or assigned role; "
                "your account is neither"
            ),
        )
    try:
        with open_session() as session:
            emp = session.get(Employee, uid)
    except Exception:
        logger.exception(
            "admin_or_assigned_gate: ORM read failed; denying access"
        )
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail="internal error verifying role",
        )
    if emp is None or emp.role not in ("admin", "assigned"):
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail=(
                "SOUL.md editing requires admin or assigned role; "
                "your account is neither"
            ),
        )
    return raw


AdminOrAssignedGate = Annotated[str, Depends(admin_or_assigned_gate)]


# -- response shapes ---------------------------------------------------------

class EmployeeOut(BaseModel):
    """The bits the list view + the detail panel both need.

    ``api_key`` is **never** included — only the ``api_key_set``
    flag (so the UI can render "configured" vs "not set") and
    the ``api_key_last4`` suffix (so the UI can show ``"sk-…abcd"``
    without leaking the value). For the actual key, the operator
    re-enters it via PATCH; we never read it back.

    ``separated_at`` is the soft-delete flag — ``None`` means
    active, a timestamp means the employee was marked separated
    at that time. The dashboard shows a "已离职" badge and the
    dedicated "已离职员工" scope filters on this.

    ``role`` is the per-MAGI-perspective classification
    (admin / employee / assigned / other). See
    :class:`magi.agent.db.Employee` for the semantics.
    ``telegram_id`` is the bound TG chat id when known
    (``None`` until the /start binding flow runs).
    """

    id: int
    name: str
    display_name: str | None = None
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    separated_at: str | None = None
    role: str = "assigned"
    telegram_id: int | None = None


# Roles the operator can assign via the API. The four
# values match the per-MAGI-perspective enum documented
# on :class:`magi.agent.db.Employee.role`.
# ``employee`` and ``guest`` are reserved for the multi-
# instance future (C6+) but the enum already supports
# them, so we don't reject manual assignments.
_EMPLOYEE_ROLES: tuple[str, ...] = (
    "admin",
    "assigned",
    "employee",
    "guest",
)


class EmployeeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    role: str = Field(default="assigned", max_length=16)
    telegram_id: int | None = None


class EmployeeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    provider: Optional[str] = Field(default=None, max_length=32)
    api_key: Optional[str] = Field(default=None, max_length=512)
    separated: Optional[bool] = None
    role: Optional[str] = Field(default=None, max_length=16)
    telegram_id: Optional[int] = None


class EmployeeListOut(BaseModel):
    """Paginated list response for ``GET /api/employees``.

    ``total`` is the number of rows matching the scope filter
    *before* pagination; ``total_pages`` is computed from it
    so the UI doesn't have to round-trip again. ``items`` is
    the page slice in the same order the SQL query produced
    (name ASC)."""

    items: list[EmployeeOut]
    total: int
    page: int
    page_size: int
    total_pages: int


def _mask_key(raw: str | None) -> tuple[bool, str | None]:
    """Return ``(is_set, last4_or_None)`` from a stored key.

    Used by every employee serialisation so the policy lives
    in one place. The ``last4`` is a usability affordance for
    the operator ("did the rotate land?") — it doesn't reveal
    the value.
    """
    if not raw:
        return False, None
    return True, (raw[-4:] if len(raw) >= 4 else raw)


def _serialize_employee(e: Employee) -> EmployeeOut:
    is_set, last4 = _mask_key(e.api_key)
    return EmployeeOut(
        id=e.id,
        name=e.name,
        display_name=e.display_name,
        provider=e.provider,
        api_key_set=is_set,
        api_key_last4=last4,
        separated_at=e.separated_at.isoformat() if e.separated_at else None,
        role=e.role,
        telegram_id=e.telegram_id,
    )


# -- scope query semantics --------------------------------------------------
#
# The scope query params are mutually exclusive — pick one:
#   - ``?unassigned=true``         — active employees with no Magi
#   - ``?separated=true``          — ALL separated employees (the
#                                    "已离职员工" scope)
# Separated employees are hidden by default in the regular
# scopes; pass ``?include_separated=true`` to fold them in.
# Results are paginated: ``page`` is 1-based, ``page_size``
# defaults to 20 and caps at 100. Response wraps the page in
# ``{items, total, page, page_size, total_pages}`` so the UI
# can render the pager without a second round-trip.
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


@router.get("/employees", response_model=EmployeeListOut)
def list_employees(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    unassigned: bool = False,
    separated: bool = False,
    include_separated: bool = False,
    role: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> EmployeeListOut:
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = _PAGE_SIZE_DEFAULT
    if page_size > _PAGE_SIZE_MAX:
        page_size = _PAGE_SIZE_MAX

    base = select(Employee)
    if separated:
        # Dedicated "已离职员工" scope: only show separated ones.
        base = base.where(Employee.separated_at.is_not(None))
    else:
        # Regular scopes hide separated employees by default.
        if not include_separated:
            base = base.where(Employee.separated_at.is_(None))
        if unassigned:
            # Reserved for the future "users.magi_id IS NULL"
            # scope; for now every employee is implicitly
            # unassigned (no Magi FK on this table).
            pass

    if role is not None:
        if role not in _EMPLOYEE_ROLES:
            raise MagiHTTPException(
                status_code=400,
                code="validation.role_unknown",
                detail=(
                    f"Unknown role {role!r}. "
                    f"Valid: {', '.join(_EMPLOYEE_ROLES)}"
                ),
            )
        base = base.where(Employee.role == role)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    page_q = (
        base.order_by(Employee.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = session.scalars(page_q).all()
    return EmployeeListOut(
        items=[_serialize_employee(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(
    payload: EmployeeCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EmployeeOut:
    name = payload.name.strip()
    if not name:
        raise MagiHTTPException(
            status_code=400,
            code="validation.name_required",
            detail="name must not be empty",
        )
    if session.scalar(select(Employee).where(Employee.name == name)) is not None:
        raise MagiHTTPException(
            status_code=409,
            code="conflict.employee_name_exists",
            detail=f"employee {name!r} already exists",
        )
    if payload.role not in _EMPLOYEE_ROLES:
        raise MagiHTTPException(
            status_code=400,
            code="validation.role_unknown",
            detail=(
                f"Unknown role {payload.role!r}. "
                f"Valid: {', '.join(_EMPLOYEE_ROLES)}"
            ),
        )
    if payload.telegram_id is not None and session.scalar(
        select(Employee).where(Employee.telegram_id == payload.telegram_id)
    ) is not None:
        raise MagiHTTPException(
            status_code=409,
            code="conflict.telegram_id_already_bound",
            detail=(
                f"telegram_id {payload.telegram_id} is already bound "
                "to another employee"
            ),
        )
    emp = Employee(
        name=name,
        display_name=payload.display_name,
        provider=payload.provider,
        api_key=payload.api_key,
        role=payload.role,
        telegram_id=payload.telegram_id,
    )
    session.add(emp)
    session.commit()
    session.refresh(emp)
    return _serialize_employee(emp)


@router.get("/employees/{emp_id}", response_model=EmployeeOut)
def get_employee(
    emp_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EmployeeOut:
    emp = session.get(Employee, emp_id)
    if emp is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.employee",
            detail="employee not found",
        )
    return _serialize_employee(emp)


@router.patch("/employees/{emp_id}", response_model=EmployeeOut)
def update_employee(
    emp_id: int,
    payload: EmployeeUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EmployeeOut:
    emp = session.get(Employee, emp_id)
    if emp is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.employee",
            detail="employee not found",
        )

    if "display_name" in payload.model_fields_set:
        emp.display_name = payload.display_name

    if "provider" in payload.model_fields_set:
        emp.provider = payload.provider

    if "api_key" in payload.model_fields_set:
        emp.api_key = payload.api_key if payload.api_key else None

    if "separated" in payload.model_fields_set:
        if payload.separated:
            emp.separated_at = utcnow_naive()
        else:
            emp.separated_at = None

    if "role" in payload.model_fields_set and payload.role is not None:
        if payload.role not in _EMPLOYEE_ROLES:
            raise MagiHTTPException(
                status_code=400,
                code="validation.role_unknown",
                detail=(
                    f"Unknown role {payload.role!r}. "
                    f"Valid: {', '.join(_EMPLOYEE_ROLES)}"
                ),
            )
        emp.role = payload.role

    if "telegram_id" in payload.model_fields_set:
        new_tg = payload.telegram_id
        if new_tg is not None:
            existing = session.scalar(
                select(Employee).where(Employee.telegram_id == new_tg)
            )
            if existing is not None and existing.id != emp.id:
                raise MagiHTTPException(
                    status_code=409,
                    code="conflict.telegram_id_already_bound",
                    detail=(
                        f"telegram_id {new_tg} is already bound to "
                        f"employee {existing.id} ({existing.name!r})"
                    ),
                )
        emp.telegram_id = new_tg

    session.commit()
    session.refresh(emp)
    return _serialize_employee(emp)


# Hard delete is intentionally not exposed. The org needs the
# historical record so separation is one-way-but-reversible —
# flip ``separated`` to ``false`` via PATCH to bring the
# employee back. If a future requirement needs a true purge,
# gate it behind a separate admin-only endpoint with explicit
# confirmation.