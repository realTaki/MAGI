"""MAGIS tree, team instruction, role, and membership APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from magi.agent.db import MAGIC, MAGIS, MAGISAdmin, MAGISMembership, MAGISRole
from magi.agent.db.magis import get_magis_session
from magi.agent.db.models_magis_membership import (
    RESERVED_ROLE_NAMES,
    adam_manages_magis,
    ensure_default_roles,
)
from magi.channels.webui.api.errors import MagiHTTPException

router = APIRouter(tags=["magis"])


def _admin_gate(request: Request) -> str:
    from magi.channels.webui.api.auth_gates import admin_gate
    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


class MAGISOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    adam_id: int | None
    instruction: str
    child_count: int = 0
    member_count: int = 0
    created_at: str
    updated_at: str


class MAGISCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = Field(default=None, ge=1)
    instruction: str = Field(default="", max_length=12000)


class MAGISUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = Field(default=None, ge=1)
    instruction: str | None = Field(default=None, max_length=12000)


class RoleOut(BaseModel):
    id: int
    magis_id: int
    name: str
    instruction: str
    is_reserved: bool


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    instruction: str = Field(default="", max_length=12000)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    instruction: str | None = Field(default=None, max_length=12000)


class MembershipOut(BaseModel):
    id: int
    magic_id: int
    magic_name: str | None = None
    role_id: int
    role_name: str


class MembershipCreate(BaseModel):
    magic_id: int = Field(ge=1)
    role_id: int = Field(ge=1)


class MembershipUpdate(BaseModel):
    role_id: int = Field(ge=1)


class MAGISAdminOut(BaseModel):
    id: int
    magis_id: int
    telegram_id: int
    display_name: str | None = None


class MAGISAdminCreate(BaseModel):
    telegram_id: int
    display_name: str | None = Field(default=None, max_length=120)


def _out(m: MAGIS, members: int = 0) -> MAGISOut:
    return MAGISOut(id=m.id, name=m.name, parent_id=m.parent_id, adam_id=m.adam_id, instruction=m.instruction, child_count=len(m.children), member_count=members, created_at=m.created_at.isoformat() if m.created_at else "", updated_at=m.updated_at.isoformat() if m.updated_at else "")


def _magis_or_404(session: Session, magis_id: int) -> MAGIS:
    magis = session.get(MAGIS, magis_id)
    if magis is None:
        raise MagiHTTPException(404, "not_found.magis", "MAGIS not found")
    return magis


def _role_or_404(session: Session, magis_id: int, role_id: int) -> MAGISRole:
    role = session.get(MAGISRole, role_id)
    if role is None or role.magis_id != magis_id:
        raise MagiHTTPException(400, "validation.magis_role_not_found", "role does not belong to this MAGIS")
    return role


def _role_out(role: MAGISRole) -> RoleOut:
    return RoleOut(id=role.id, magis_id=role.magis_id, name=role.name, instruction=role.instruction, is_reserved=role.is_reserved)


def _membership_out(row: tuple[MAGISMembership, MAGIC, MAGISRole]) -> MembershipOut:
    membership, magic, role = row
    return MembershipOut(id=membership.id, magic_id=magic.id, magic_name=magic.name, role_id=role.id, role_name=role.name)


def _admin_out(row: MAGISAdmin) -> MAGISAdminOut:
    return MAGISAdminOut(id=row.id, magis_id=row.magis_id, telegram_id=row.telegram_id, display_name=row.display_name)


def _serving_adam(session: Session) -> int | None:
    """MAGI identity for this WebUI process, if it is an Adam node.

    Authentication still verifies the human administrator. This check adds
    the MAGIS-tree scope of the Adam whose WebUI they are operating.
    """
    import os
    runtime_id = os.environ.get("MAGI_RUNTIME_ID")
    if runtime_id and runtime_id.isdigit():
        return int(runtime_id)
    root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
    return root.adam_id if root else None


def _served_direct_magis_id(session: Session) -> int | None:
    """The one MAGIS whose data this runtime may administer."""
    actor = _serving_adam(session)
    if actor is not None:
        membership = session.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == actor))
        if membership is not None:
            return membership.magis_id
    root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
    return root.id if root else None


def _require_managed(session: Session, magis_id: int) -> None:
    served = _served_direct_magis_id(session)
    if served != magis_id:
        raise MagiHTTPException(403, "forbidden.magis_management_scope", "MAGIS administration is limited to the current MAGI's direct MAGIS")


@router.get("/magis", response_model=list[MAGISOut])
def list_magis(_admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> list[MAGISOut]:
    served = _served_direct_magis_id(session)
    rows = session.scalars(select(MAGIS).options(selectinload(MAGIS.children)).where(MAGIS.id == served).order_by(MAGIS.name)).all() if served else []
    counts = dict(session.execute(select(MAGISMembership.magis_id, func.count()).group_by(MAGISMembership.magis_id)).all())
    return [_out(row, counts.get(row.id, 0)) for row in rows]


@router.post("/magis", response_model=MAGISOut, status_code=201)
def create_magis(payload: MAGISCreate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGISOut:
    if payload.parent_id is not None:
        _magis_or_404(session, payload.parent_id)
        _require_managed(session, payload.parent_id)
    if session.scalar(select(MAGIS.id).where(MAGIS.name == payload.name)):
        raise MagiHTTPException(400, "validation.magis_name_duplicate", "MAGIS name already exists")
    magis = MAGIS(name=payload.name, parent_id=payload.parent_id, instruction=payload.instruction)
    session.add(magis)
    session.flush()
    ensure_default_roles(session, magis.id)
    session.commit()
    # Provisioning is control-plane work. A MAGI never gets Kubernetes API
    # credentials just because it manages a MAGIS.
    try:
        from magi.orchestrator.client import provision_magis
        from magi.orchestrator.contracts import MagisBinding
        provision_magis(MagisBinding(id=magis.id, name=magis.name))
    except Exception as exc:
        raise MagiHTTPException(503, "runtime.magis_provisioning_unavailable", str(exc)) from exc
    session.refresh(magis, ["children"])
    return _out(magis)


@router.get("/magis/{magis_id}", response_model=MAGISOut)
def get_magis(magis_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGISOut:
    m = _magis_or_404(session, magis_id)
    session.refresh(m, ["children"])
    count = session.scalar(select(func.count()).select_from(MAGISMembership).where(MAGISMembership.magis_id == magis_id)) or 0
    return _out(m, count)


@router.patch("/magis/{magis_id}", response_model=MAGISOut)
def update_magis(magis_id: int, payload: MAGISUpdate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGISOut:
    m = _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    if payload.name is not None and payload.name != m.name:
        duplicate = session.scalar(select(MAGIS.id).where(MAGIS.name == payload.name))
        if duplicate and duplicate != magis_id:
            raise MagiHTTPException(400, "validation.magis_name_duplicate", "MAGIS name already exists")
        m.name = payload.name
    if payload.instruction is not None:
        m.instruction = payload.instruction
    if "parent_id" in payload.model_fields_set:
        if payload.parent_id == magis_id:
            raise MagiHTTPException(400, "validation.magis_self_parent", "a MAGIS cannot be its own parent")
        if payload.parent_id is not None:
            cursor = _magis_or_404(session, payload.parent_id)
            while cursor is not None:
                if cursor.id == magis_id:
                    raise MagiHTTPException(400, "validation.magis_cycle", "parent assignment would create a cycle")
                cursor = session.get(MAGIS, cursor.parent_id) if cursor.parent_id else None
        m.parent_id = payload.parent_id
    session.commit()
    session.refresh(m, ["children"])
    count = session.scalar(select(func.count()).select_from(MAGISMembership).where(MAGISMembership.magis_id == magis_id)) or 0
    return _out(m, count)


@router.delete("/magis/{magis_id}", status_code=204)
def delete_magis(magis_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> Response:
    m = _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    session.execute(update(MAGIS).where(MAGIS.parent_id == magis_id).values(parent_id=m.parent_id))
    session.delete(m)
    session.commit()
    return Response(status_code=204)


@router.get("/magis/{magis_id}/roles", response_model=list[RoleOut])
def list_roles(magis_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> list[RoleOut]:
    _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    ensure_default_roles(session, magis_id)
    session.commit()
    return [_role_out(r) for r in session.scalars(select(MAGISRole).where(MAGISRole.magis_id == magis_id).order_by(MAGISRole.is_reserved.desc(), MAGISRole.name)).all()]


@router.post("/magis/{magis_id}/roles", response_model=RoleOut, status_code=201)
def create_role(magis_id: int, payload: RoleCreate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> RoleOut:
    _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    if payload.name in RESERVED_ROLE_NAMES:
        raise MagiHTTPException(400, "validation.role_name_reserved", "Adam and EVE are reserved role names")
    if session.scalar(select(MAGISRole.id).where(MAGISRole.magis_id == magis_id, MAGISRole.name == payload.name)):
        raise MagiHTTPException(400, "validation.role_name_duplicate", "role name already exists in this MAGIS")
    role = MAGISRole(magis_id=magis_id, name=payload.name, instruction=payload.instruction)
    session.add(role); session.commit(); session.refresh(role)
    return _role_out(role)


@router.patch("/magis/{magis_id}/roles/{role_id}", response_model=RoleOut)
def update_role(magis_id: int, role_id: int, payload: RoleUpdate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> RoleOut:
    _require_managed(session, magis_id)
    role = _role_or_404(session, magis_id, role_id)
    if role.is_reserved:
        raise MagiHTTPException(403, "forbidden.reserved_role", "reserved roles cannot be edited")
    if payload.name is not None:
        role.name = payload.name
    if payload.instruction is not None:
        role.instruction = payload.instruction
    session.commit(); return _role_out(role)


@router.delete("/magis/{magis_id}/roles/{role_id}", status_code=204)
def delete_role(magis_id: int, role_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> Response:
    _require_managed(session, magis_id)
    role = _role_or_404(session, magis_id, role_id)
    if role.is_reserved:
        raise MagiHTTPException(403, "forbidden.reserved_role", "reserved roles cannot be deleted")
    if session.scalar(select(MAGISMembership.id).where(MAGISMembership.role_id == role.id)):
        raise MagiHTTPException(409, "validation.role_in_use", "reassign members before deleting this role")
    session.delete(role); session.commit(); return Response(status_code=204)


@router.get("/magis/{magis_id}/memberships", response_model=list[MembershipOut])
def list_memberships(magis_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> list[MembershipOut]:
    _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    rows = session.execute(select(MAGISMembership, MAGIC, MAGISRole).join(MAGIC, MAGIC.id == MAGISMembership.magic_id).join(MAGISRole, MAGISRole.id == MAGISMembership.role_id).where(MAGISMembership.magis_id == magis_id).order_by(MAGISMembership.id)).all()
    return [_membership_out(row) for row in rows]


def _assign_adam(m: MAGIS, role: MAGISRole, magic_id: int) -> None:
    if role.name == "Adam":
        if m.adam_id is not None and m.adam_id != magic_id:
            raise MagiHTTPException(409, "validation.adam_already_assigned", "this MAGIS already has an Adam")
        m.adam_id = magic_id
    elif m.adam_id == magic_id:
        m.adam_id = None


@router.post("/magis/{magis_id}/memberships", response_model=MembershipOut, status_code=201)
def create_membership(magis_id: int, payload: MembershipCreate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MembershipOut:
    m = _magis_or_404(session, magis_id); role = _role_or_404(session, magis_id, payload.role_id)
    _require_managed(session, magis_id)
    magic = session.get(MAGIC, payload.magic_id)
    if magic is None: raise MagiHTTPException(404, "not_found.magic", "MAGI not found")
    existing = session.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == magic.id))
    if existing is not None:
        raise MagiHTTPException(409, "validation.magic_already_assigned", "a MAGI can have only one direct MAGIS membership")
    _assign_adam(m, role, magic.id)
    membership = MAGISMembership(magis_id=magis_id, magic_id=magic.id, role_id=role.id)
    session.add(membership); session.commit(); session.refresh(membership)
    return _membership_out((membership, magic, role))


@router.patch("/magis/{magis_id}/memberships/{membership_id}", response_model=MembershipOut)
def update_membership(magis_id: int, membership_id: int, payload: MembershipUpdate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MembershipOut:
    m = _magis_or_404(session, magis_id); membership = session.get(MAGISMembership, membership_id)
    _require_managed(session, magis_id)
    if membership is None or membership.magis_id != magis_id: raise MagiHTTPException(404, "not_found.membership", "membership not found")
    role = _role_or_404(session, magis_id, payload.role_id)
    _assign_adam(m, role, membership.magic_id)
    membership.role_id = role.id; session.commit()
    return _membership_out((membership, _magic := session.get(MAGIC, membership.magic_id), role))


@router.delete("/magis/{magis_id}/memberships/{membership_id}", status_code=204)
def delete_membership(magis_id: int, membership_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> Response:
    m = _magis_or_404(session, magis_id); membership = session.get(MAGISMembership, membership_id)
    _require_managed(session, magis_id)
    if membership is None or membership.magis_id != magis_id: raise MagiHTTPException(404, "not_found.membership", "membership not found")
    if m.adam_id == membership.magic_id: m.adam_id = None
    session.delete(membership); session.commit(); return Response(status_code=204)


@router.get("/magis/{magis_id}/admins", response_model=list[MAGISAdminOut])
def list_magis_admins(magis_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> list[MAGISAdminOut]:
    _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    return [_admin_out(row) for row in session.scalars(select(MAGISAdmin).where(MAGISAdmin.magis_id == magis_id).order_by(MAGISAdmin.id)).all()]


@router.post("/magis/{magis_id}/admins", response_model=MAGISAdminOut, status_code=201)
def add_magis_admin(magis_id: int, payload: MAGISAdminCreate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGISAdminOut:
    _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    existing = session.scalar(select(MAGISAdmin).where(MAGISAdmin.magis_id == magis_id, MAGISAdmin.telegram_id == payload.telegram_id))
    if existing is not None:
        if payload.display_name is not None:
            existing.display_name = payload.display_name
            session.commit()
        return _admin_out(existing)
    row = MAGISAdmin(magis_id=magis_id, telegram_id=payload.telegram_id, display_name=payload.display_name)
    session.add(row); session.commit(); session.refresh(row)
    return _admin_out(row)


@router.delete("/magis/{magis_id}/admins/{admin_id}", status_code=204)
def delete_magis_admin(magis_id: int, admin_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> Response:
    _magis_or_404(session, magis_id)
    _require_managed(session, magis_id)
    row = session.get(MAGISAdmin, admin_id)
    if row is None or row.magis_id != magis_id:
        raise MagiHTTPException(404, "not_found.magis_admin", "MAGIS admin not found")
    session.delete(row); session.commit()
    return Response(status_code=204)
