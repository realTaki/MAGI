"""Department + (minimal) Employee API for the Organization tab.

C1.1: departments with a tree structure (parent_id self-FK) and a
manager_id pointing at employees. C1.2 will grow the employee side
into the full directory / TG-binding / EVE-assignment story; for
now ``Employee`` is just enough to back the "manager" picker.

All routes require the user to be signed in (the existing
``/api/auth/me`` check) and to be an admin (an Employee row
with ``role='admin'``). Both checks run in the ``require_admin``
dependency — keeping the auth gate in one place so the route
bodies stay focused on the data.
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
from sqlalchemy.orm import Session, selectinload

from magi.agent.db import (
    Department,
    Employee,
    get_session,
)

logger = logging.getLogger("magi.api.departments")

router = APIRouter(tags=["departments"])


# -- auth gate --------------------------------------------------------------

def _is_admin_employee_id(employee_id: int) -> bool:
    """True if the ``Employee`` row with the given id has role=admin.

    D.24: the ``magi_session`` cookie carries the
    ``employee_id`` (cross-channel identity), not the chat_id.
    The admin allowlist is keyed by employee id; a future
    channel will resolve its own delivery address to the same
    employee id and re-use this same check.

    An ORM read failure (table not yet initialised, etc.) is a
    hard ``False`` — the gate fails closed rather than silently
    letting unauthenticated callers through. ``AdminGate`` is
    the only auth path; the chat endpoint and action_items
    endpoint both pre-check via this same gate.
    """
    from magi.agent.db import Employee, open_session

    try:
        with open_session() as session:
            emp = session.get(Employee, employee_id)
            if emp is not None and emp.role == "admin":
                return True
    except Exception:
        logger.exception("admin_gate: ORM read failed; denying access")

    return False


def admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is a super admin.

    D.24: reads the ``employee_id`` from the cookie and
    looks up the row's role directly. The auth router
    validates the same cookie in its ``/me`` handler, so by
    the time a request gets here the cookie is known to be a
    live session; this gate just re-checks the caller is
    still in the super-admins list (a stale cookie after an
    admin removal shouldn't sneak past).

    Returns the cookie's employee_id as a string for
    call-site convenience (the chat_sessions router casts it
    back to ``int``).
    """
    raw = request.cookies.get("magi_session")
    if not raw or not raw.isdigit():
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    employee_id = int(raw)
    if not _is_admin_employee_id(employee_id):
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    return raw


AdminGate = Annotated[str, Depends(admin_gate)]


def _is_admin_or_assigned_chat_id(chat_id: str) -> bool:
    """True if ``chat_id`` resolves to an employee with role
    in ``{'admin', 'assigned'}``.

    Used by routes that aren't admin-only but also aren't
    public — currently the soul editor (``/api/soul``) which
    the spec lets both admins and assigned employees (the
    "served employee" of this MAGI node) touch. Employee /
    guest roles stay locked out.

    Mirrors :func:`_is_admin_chat_id` so a swap of role names
    in the future touches one place per gate.
    """
    from sqlalchemy import select

    from magi.agent.db import Employee, open_session

    if not chat_id:
        return False
    try:
        cid_int = int(chat_id)
    except (TypeError, ValueError):
        return False
    try:
        with open_session() as session:
            emp = session.scalar(
                select(Employee).where(Employee.telegram_id == cid_int)
            )
            if emp is not None and emp.role in ("admin", "assigned"):
                return True
    except Exception:
        logger.exception(
            "admin_or_assigned_gate: ORM read failed; denying access"
        )
    return False


def admin_or_assigned_gate(request: Request) -> str:
    """FastAPI dependency — ``admin`` or ``assigned`` employee.

    Read paths (GET) and write paths (PUT/POST) on
    ``/api/soul`` both gate through this; the soul editor is
    the first feature where ``assigned`` employees get a
    write surface, but they don't get full admin powers
    (department CRUD, employee CRUD, settings etc. stay
    admin-only).
    """
    chat_id = request.cookies.get("magi_session")
    if not chat_id or not _is_admin_or_assigned_chat_id(chat_id):
        raise MagiHTTPException(
            status_code=403,
            code="auth.soul_edit_forbidden",
            detail=(
                "SOUL.md editing requires admin or assigned role; "
                "your account is neither"
            ),
        )
    return chat_id


AdminOrAssignedGate = Annotated[str, Depends(admin_or_assigned_gate)]


# -- response shapes ---------------------------------------------------------

class EmployeeBrief(BaseModel):
    """The bits of an employee the department list needs to render
    a "manager" column without an extra round-trip per row."""

    id: int
    name: str
    display_name: str | None = None


class DepartmentOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    manager: EmployeeBrief | None = None
    # ``child_count`` is the number of direct (one-level-down)
    # departments. Total headcount would join through employees
    # — out of scope for C1.1.
    child_count: int = 0
    created_at: str
    updated_at: str


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = None
    manager_id: int | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = None  # explicit null moves dept to root
    manager_id: int | None = None  # explicit null un-assigns manager


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
    department_id: int | None = None
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
    # Provider / api_key can be set at create time, but most
    # of the time the operator will add a placeholder and fill
    # these in later via PATCH. Optional to keep the create
    # flow light.
    department_id: int | None = None
    provider: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    # Role defaults to "assigned" — in v0 single-instance,
    # this MAGI serves every employee by default. The
    # onboarding wizard (step 3) sets role=admin explicitly
    # for super admins. ``telegram_id`` is optional at create;
    # the /start binding flow (C2) sets it later.
    role: str = Field(default="assigned", max_length=16)
    telegram_id: int | None = None


class EmployeeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    department_id: Optional[int] = None  # explicit null un-assigns
    provider: Optional[str] = Field(default=None, max_length=32)
    # ``api_key`` is write-only. Set it to a new value to
    # rotate; set it to an empty string to clear. ``None`` means
    # "don't change".
    api_key: Optional[str] = Field(default=None, max_length=512)
    # Soft-delete toggle. ``true`` stamps ``separated_at = now``;
    # ``false`` clears it back to NULL (the employee is restored).
    # ``None`` means "don't change". Distinct from "absent" via
    # ``model_fields_set`` like the other optional fields.
    separated: Optional[bool] = None
    # Role transition. ``None`` means "don't change". To
    # un-assign a TG chat, send ``telegram_id=null``; the
    # endpoint validates uniqueness so a duplicate id is
    # a 409, not a silent overwrite.
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
        department_id=e.department_id,
        provider=e.provider,
        api_key_set=is_set,
        api_key_last4=last4,
        separated_at=e.separated_at.isoformat() if e.separated_at else None,
        role=e.role,
        telegram_id=e.telegram_id,
    )


# -- helpers -----------------------------------------------------------------

def _would_close_loop(
    session: Session, dept_id: int | None, new_parent_id: int | None
) -> bool:
    """True if setting ``dept_id``'s parent to ``new_parent_id``
    would create a cycle.

    A cycle means ``new_parent_id`` is a descendant of
    ``dept_id``. Walk down from ``new_parent_id`` following
    parent_id; if we ever land on ``dept_id``, the move
    would close a loop.
    """
    if new_parent_id is None:
        return False
    if dept_id is None:
        return False
    if new_parent_id == dept_id:
        return True
    cursor: int | None = new_parent_id
    seen: set[int] = set()
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        if cursor == dept_id:
            return True
        parent_id = session.get(Department, cursor)
        if parent_id is None:
            return False
        cursor = parent_id.parent_id
    return False


def _serialize(d: Department) -> DepartmentOut:
    return DepartmentOut(
        id=d.id,
        name=d.name,
        parent_id=d.parent_id,
        manager=(
            EmployeeBrief(
                id=d.manager.id,
                name=d.manager.name,
                display_name=d.manager.display_name,
            )
            if d.manager is not None
            else None
        ),
        child_count=len(d.children),
        created_at=d.created_at.isoformat() if d.created_at else "",
        updated_at=d.updated_at.isoformat() if d.updated_at else "",
    )


# -- department endpoints ---------------------------------------------------

@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> list[DepartmentOut]:
    """Return every department as a flat list, with parent_id +
    child_count so the frontend can render the tree without
    needing a second request.

    Sorted by name for stable display order; the frontend
    groups by parent_id and indents to show the hierarchy.
    """
    depts = session.scalars(
        select(Department)
        .options(selectinload(Department.children), selectinload(Department.manager))
        .order_by(Department.name.asc())
    ).all()
    return [_serialize(d) for d in depts]


@router.post("/departments", response_model=DepartmentOut, status_code=201)
def create_department(
    payload: DepartmentCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> DepartmentOut:
    name = payload.name.strip()
    if not name:
        raise MagiHTTPException(
            status_code=400,
            code="validation.name_required",
            detail="name must not be empty",
        )

    # Parent + manager validation. ``get`` on a non-existent PK
    # returns None in SQLAlchemy 2.x, so we explicitly 404.
    if payload.parent_id is not None:
        parent = session.get(Department, payload.parent_id)
        if parent is None:
            raise MagiHTTPException(
                status_code=400,
                code="validation.department_id_not_found",
                detail=f"parent_id {payload.parent_id} not found",
            )
    if payload.manager_id is not None:
        manager = session.get(Employee, payload.manager_id)
        if manager is None:
            raise MagiHTTPException(
                status_code=400,
                code="validation.manager_id_not_found",
                detail=f"manager_id {payload.manager_id} not found",
            )

    # Name uniqueness is enforced at the SQL level (unique=True
    # on the column) — surface that as a 409 here.
    if session.scalar(select(Department).where(Department.name == name)) is not None:
        raise MagiHTTPException(
            status_code=409,
            code="conflict.department_name_exists",
            detail=f"department {name!r} already exists",
        )

    dept = Department(
        name=name,
        parent_id=payload.parent_id,
        manager_id=payload.manager_id,
    )
    session.add(dept)
    session.commit()
    # Re-fetch with the relationships loaded so the response
    # includes manager + child_count.
    session.refresh(dept)
    return _serialize(dept)


@router.get("/departments/{dept_id}", response_model=DepartmentOut)
def get_department(
    dept_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> DepartmentOut:
    dept = session.get(
        Department, dept_id, options=[selectinload(Department.children), selectinload(Department.manager)]
    )
    if dept is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.department",
            detail="department not found",
        )
    return _serialize(dept)


@router.patch("/departments/{dept_id}", response_model=DepartmentOut)
def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> DepartmentOut:
    dept = session.get(
        Department, dept_id, options=[selectinload(Department.children), selectinload(Department.manager)]
    )
    if dept is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.department",
            detail="department not found",
        )

    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise MagiHTTPException(
            status_code=400,
            code="validation.name_required",
            detail="name must not be empty",
        )
        if new_name != dept.name and session.scalar(
            select(Department).where(Department.name == new_name)
        ) is not None:
            raise MagiHTTPException(
                status_code=409,
                code="conflict.department_name_exists",
                detail=f"department {new_name!r} already exists",
            )
        dept.name = new_name

    # parent_id is optional in the payload; explicit null moves
    # the dept to the top level. ``model_fields_set`` lets us
    # distinguish "not sent" from "sent as null".
    if "parent_id" in payload.model_fields_set:
        if payload.parent_id is not None:
            parent = session.get(Department, payload.parent_id)
            if parent is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.department_id_not_found",
                    detail=f"parent_id {payload.parent_id} not found",
                )
            if _would_close_loop(session, dept_id, payload.parent_id):
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.parent_change_creates_cycle",
                    detail="parent change would create a cycle",
                )
        dept.parent_id = payload.parent_id

    if "manager_id" in payload.model_fields_set:
        if payload.manager_id is not None:
            manager = session.get(Employee, payload.manager_id)
            if manager is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.manager_id_not_found",
                    detail=f"manager_id {payload.manager_id} not found",
                )
        dept.manager_id = payload.manager_id

    session.commit()
    session.refresh(dept)
    return _serialize(dept)


@router.delete("/departments/{dept_id}", status_code=204)
def delete_department(
    dept_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    dept = session.get(
        Department, dept_id, options=[selectinload(Department.children)]
    )
    if dept is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.department",
            detail="department not found",
        )
    if dept.children:
        # Refuse rather than cascade — a delete with sub-
        # departments is almost always a mistake. The deployer
        # should move / delete the children first.
        names = ", ".join(c.name for c in dept.children[:5])
        more = "" if len(dept.children) <= 5 else f" (+{len(dept.children) - 5} more)"
        raise MagiHTTPException(
            status_code=409,
            code="conflict.department_has_subdepts",
            detail=f"department has {len(dept.children)} sub-departments: {names}{more}",
        )
    session.delete(dept)
    session.commit()
    return Response(status_code=204)


# -- employee endpoints (minimal) -------------------------------------------
#
# C1.1 only needs this much for the manager picker. The full
# employee directory / TG binding / EVE assignment lands with
# C1.2 + C1.3 + C2 in a separate router that supersedes this.

employees_router = APIRouter(tags=["employees"])


# The scope query params are mutually exclusive — pick one:
#   - ``?department_id=X``         — active employees in dept X
#   - ``?unassigned=true``         — active employees with no dept
#   - ``?separated=true``          — ALL separated employees (the
#                                    "已离职员工" scope)
# Separated employees are hidden by default in the regular
# scopes; pass ``?include_separated=true`` to fold them in
# (the dept view's "显示离职员工" toggle).
# Results are paginated: ``page`` is 1-based, ``page_size``
# defaults to 20 and caps at 100. Response wraps the page in
# ``{items, total, page, page_size, total_pages}`` so the UI
# can render the pager without a second round-trip.
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


@employees_router.get("/employees", response_model=EmployeeListOut)
def list_employees(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    department_id: int | None = None,
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
        # Dedicated "已离职员工" scope: only show separated ones,
        # regardless of which dept they belonged to.
        base = base.where(Employee.separated_at.is_not(None))
    else:
        # Regular scopes (department / unassigned) hide
        # separated employees by default; the UI flips the
        # ``include_separated`` toggle to see them.
        if not include_separated:
            base = base.where(Employee.separated_at.is_(None))
        if unassigned:
            base = base.where(Employee.department_id.is_(None))
        elif department_id is not None:
            base = base.where(Employee.department_id == department_id)

    # ``role`` filter — drives the WebUI Access card
    # (role=admin) and the future "assigned to me" pane
    # (role=assigned). Validated here rather than in the
    # SQL ``WHERE`` so a typo gets a 400, not an empty list.
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

    # COUNT comes from a separate statement against the same
    # WHERE clause so the pager has the true total.
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


@employees_router.post("/employees", response_model=EmployeeOut, status_code=201)
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
    # Validate the optional FK up-front so a bad ID gets a 400
    # rather than a confusing 500 from SQLite.
    if payload.department_id is not None and session.get(Department, payload.department_id) is None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.department_id_not_found",
            detail=f"department_id {payload.department_id} not found",
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
    # Telegram id uniqueness — one chat_id binds to at most
    # one employee. A duplicate id here means the operator
    # is double-binding; surface as a 409.
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
        department_id=payload.department_id,
        provider=payload.provider,
        api_key=payload.api_key,
        role=payload.role,
        telegram_id=payload.telegram_id,
    )
    session.add(emp)
    session.commit()
    session.refresh(emp)
    return _serialize_employee(emp)


@employees_router.get("/employees/{emp_id}", response_model=EmployeeOut)
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


@employees_router.patch("/employees/{emp_id}", response_model=EmployeeOut)
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

    if "department_id" in payload.model_fields_set:
        if payload.department_id is not None:
            if session.get(Department, payload.department_id) is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.department_id_not_found",
                    detail=f"department_id {payload.department_id} not found",
                )
        emp.department_id = payload.department_id

    if "provider" in payload.model_fields_set:
        emp.provider = payload.provider

    # api_key is write-only:
    #   - None  : don't change
    #   - ""    : clear
    #   - "<x>" : set / rotate to <x>
    if "api_key" in payload.model_fields_set:
        emp.api_key = payload.api_key if payload.api_key else None

    # Soft-delete toggle. ``separated=True`` stamps
    # ``separated_at`` to now; ``separated=False`` clears it
    # (restores the employee to active). ``None`` / absent
    # means "don't touch", which is why we gate on
    # ``model_fields_set`` rather than the value.
    if "separated" in payload.model_fields_set:
        if payload.separated:
            emp.separated_at = utcnow_naive()
        else:
            emp.separated_at = None

    # Role transition. Validated against the enum so a typo
    # doesn't sneak a bad value into the DB. ``None`` means
    # "don't change", so sending the field without a value
    # is a no-op (use ``model_fields_set`` like the others).
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

    # Telegram binding. ``None`` means "don't change"; the
    # operator can send ``telegram_id=null`` to unbind (the
    # /start flow re-binds via this same field). Duplicate
    # id check is the same as create_employee — surface as
    # 409 so the operator knows to unbind the other row first.
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
# historical record (manager_of, audit trail, past assignments)
# so separation is one-way-but-reversible — flip ``separated``
# to ``false`` via PATCH to bring the employee back. If a
# future requirement needs a true purge, gate it behind a
# separate admin-only endpoint with explicit confirmation.
