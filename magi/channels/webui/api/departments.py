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
from typing import Annotated

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
    id: int
    name: str
    display_name: str | None = None


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


@employees_router.get("/employees", response_model=list[EmployeeOut])
def list_employees(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> list[EmployeeOut]:
    rows = session.scalars(
        select(Employee).order_by(Employee.name.asc())
    ).all()
    return [
        EmployeeOut(id=r.id, name=r.name, display_name=r.display_name)
        for r in rows
    ]


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
    emp = Employee(name=name, display_name=payload.display_name)
    session.add(emp)
    session.commit()
    session.refresh(emp)
    return EmployeeOut(id=emp.id, name=emp.name, display_name=emp.display_name)
