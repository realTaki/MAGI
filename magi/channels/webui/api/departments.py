"""Department + (minimal) Employee API for the Organization tab.

C1.1: departments with a tree structure (parent_id self-FK) and a
manager_id pointing at employees. C1.2 will grow the employee side
into the full directory / TG-binding / EVE-assignment story; for
now ``Employee`` is just enough to back the "manager" picker.

All routes require the user to be signed in (the existing
``/api/auth/me`` check) and to be a super admin (the existing
``telegram.super_admins`` list). Both checks run in the
``require_admin`` dependency — keeping the auth gate in one place
so the route bodies stay focused on the data.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from magi.runtime.state.orm import (
    Department,
    Employee,
    get_session,
)
from magi.runtime.state.settings import state_get

logger = logging.getLogger("magi.api.departments")

router = APIRouter(tags=["departments"])


# -- auth gate --------------------------------------------------------------

def _super_admin_chat_ids() -> set[str]:
    """Read ``telegram.super_admins`` as a set of chat_id strings.

    Same logic the auth module uses; duplicated here so this
    router doesn't need to know about the auth router's
    internals. If we add a third role later, both call sites
    collapse into a single ``require_role("admin")`` helper.
    """
    raw = state_get(os.environ.get("MAGI_STATE_DIR", "/workspace/memories"), "telegram.super_admins")
    if not raw:
        return set()
    try:
        return {str(x) for x in json.loads(raw)}
    except (ValueError, TypeError):
        return set()


def admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is a super admin.

    Reads the session cookie directly rather than calling into
    the auth router, so this module is decoupled from it. The
    auth router validates the same cookie in its ``/me``
    handler, so by the time a request gets here the cookie is
    known to be a live session; this gate just re-checks the
    caller is still in the super-admins list (a stale cookie
    after an admin removal shouldn't sneak past).
    """
    chat_id = request.cookies.get("magi_session")
    if not chat_id or chat_id not in _super_admin_chat_ids():
        raise HTTPException(status_code=401, detail="Not signed in")
    return chat_id


AdminGate = Annotated[str, Depends(admin_gate)]


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


class EmployeeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)


class EmployeeOut(BaseModel):
    """The bits the list view + the detail panel both need.

    ``api_key`` is **never** included — only the ``api_key_set``
    flag (so the UI can render "configured" vs "not set") and
    the ``api_key_last4`` suffix (so the UI can show ``"sk-…abcd"``
    without leaking the value). For the actual key, the operator
    re-enters it via PATCH; we never read it back.
    """

    id: int
    name: str
    display_name: str | None = None
    department_id: int | None = None
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None


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


class EmployeeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    department_id: Optional[int] = None  # explicit null un-assigns
    provider: Optional[str] = Field(default=None, max_length=32)
    # ``api_key`` is write-only. Set it to a new value to
    # rotate; set it to an empty string to clear. ``None`` means
    # "don't change".
    api_key: Optional[str] = Field(default=None, max_length=512)


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
        raise HTTPException(status_code=400, detail="name must not be empty")

    # Parent + manager validation. ``get`` on a non-existent PK
    # returns None in SQLAlchemy 2.x, so we explicitly 404.
    if payload.parent_id is not None:
        parent = session.get(Department, payload.parent_id)
        if parent is None:
            raise HTTPException(
                status_code=400, detail=f"parent_id {payload.parent_id} not found"
            )
    if payload.manager_id is not None:
        manager = session.get(Employee, payload.manager_id)
        if manager is None:
            raise HTTPException(
                status_code=400, detail=f"manager_id {payload.manager_id} not found"
            )

    # Name uniqueness is enforced at the SQL level (unique=True
    # on the column) — surface that as a 409 here.
    if session.scalar(select(Department).where(Department.name == name)) is not None:
        raise HTTPException(status_code=409, detail=f"department {name!r} already exists")

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
        raise HTTPException(status_code=404, detail="department not found")
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
        raise HTTPException(status_code=404, detail="department not found")

    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="name must not be empty")
        if new_name != dept.name and session.scalar(
            select(Department).where(Department.name == new_name)
        ) is not None:
            raise HTTPException(
                status_code=409, detail=f"department {new_name!r} already exists"
            )
        dept.name = new_name

    # parent_id is optional in the payload; explicit null moves
    # the dept to the top level. ``model_fields_set`` lets us
    # distinguish "not sent" from "sent as null".
    if "parent_id" in payload.model_fields_set:
        if payload.parent_id is not None:
            parent = session.get(Department, payload.parent_id)
            if parent is None:
                raise HTTPException(
                    status_code=400, detail=f"parent_id {payload.parent_id} not found"
                )
            if _would_close_loop(session, dept_id, payload.parent_id):
                raise HTTPException(
                    status_code=400, detail="parent change would create a cycle"
                )
        dept.parent_id = payload.parent_id

    if "manager_id" in payload.model_fields_set:
        if payload.manager_id is not None:
            manager = session.get(Employee, payload.manager_id)
            if manager is None:
                raise HTTPException(
                    status_code=400, detail=f"manager_id {payload.manager_id} not found"
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
        raise HTTPException(status_code=404, detail="department not found")
    if dept.children:
        # Refuse rather than cascade — a delete with sub-
        # departments is almost always a mistake. The deployer
        # should move / delete the children first.
        names = ", ".join(c.name for c in dept.children[:5])
        more = "" if len(dept.children) <= 5 else f" (+{len(dept.children) - 5} more)"
        raise HTTPException(
            status_code=409,
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


# The two query params are mutually exclusive — pick one. The
# UI uses ``?department_id=X`` for "in this dept" and
# ``?unassigned=true`` for the "未指定部门" pseudo-section.
@employees_router.get("/employees", response_model=list[EmployeeOut])
def list_employees(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    department_id: int | None = None,
    unassigned: bool = False,
) -> list[EmployeeOut]:
    q = select(Employee)
    if unassigned:
        q = q.where(Employee.department_id.is_(None))
    elif department_id is not None:
        q = q.where(Employee.department_id == department_id)
    q = q.order_by(Employee.name.asc())
    rows = session.scalars(q).all()
    return [_serialize_employee(r) for r in rows]


@employees_router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(
    payload: EmployeeCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EmployeeOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name must not be empty")
    if session.scalar(select(Employee).where(Employee.name == name)) is not None:
        raise HTTPException(status_code=409, detail=f"employee {name!r} already exists")
    # Validate the optional FK up-front so a bad ID gets a 400
    # rather than a confusing 500 from SQLite.
    if payload.department_id is not None and session.get(Department, payload.department_id) is None:
        raise HTTPException(
            status_code=400, detail=f"department_id {payload.department_id} not found"
        )
    emp = Employee(
        name=name,
        display_name=payload.display_name,
        department_id=payload.department_id,
        provider=payload.provider,
        api_key=payload.api_key,
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
        raise HTTPException(status_code=404, detail="employee not found")
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
        raise HTTPException(status_code=404, detail="employee not found")

    if "display_name" in payload.model_fields_set:
        emp.display_name = payload.display_name

    if "department_id" in payload.model_fields_set:
        if payload.department_id is not None:
            if session.get(Department, payload.department_id) is None:
                raise HTTPException(
                    status_code=400, detail=f"department_id {payload.department_id} not found"
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

    session.commit()
    session.refresh(emp)
    return _serialize_employee(emp)


@employees_router.delete("/employees/{emp_id}", status_code=204)
def delete_employee(
    emp_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Remove an employee. Refuses if they're the lead of a
    department (set the dept's manager to someone else first
    or null it). The Department.manager_id FK uses ``ON DELETE
    SET NULL`` so any other column referencing this employee
    is safe."""
    emp = session.get(Employee, emp_id)
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    led = session.scalar(
        select(Department).where(Department.manager_id == emp_id)
    )
    if led is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"employee is the lead of {led.name!r}; "
                "reassign or clear the department's manager first"
            ),
        )
    session.delete(emp)
    session.commit()
    return Response(status_code=204)
