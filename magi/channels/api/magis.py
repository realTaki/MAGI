"""MAGIS tree, team instruction, role, and membership APIs.

All data access goes through the bus facade — no ``magi.db.*`` imports
(``channels → db`` boundary enforced by
``tests/architecture/test_import_boundaries.py``).
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from magi.bus import Bus
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

router = APIRouter(tags=["magis"])


def _admin_gate(request: Request) -> str:
    from magi.channels.api.auth_gates import admin_gate
    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


# -- Pydantic response models (no ORM imports) -------------------------


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
    """Create a new MAGIC identity in this MAGIS.

    ``magis_memberships.id`` *is* the MAGIC id.  There is no separate
    MAGIC record that can be attached later, so accepting ``magic_id`` here
    would either be impossible or silently ignored.  Reject unknown fields to
    make that model boundary visible to API clients.
    """

    model_config = ConfigDict(extra="forbid")
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


# -- Conversion helpers -------------------------------------------------


def _magis_out(bus: Bus, view) -> MAGISOut:
    return MAGISOut(
        id=view.id,
        name=view.name,
        parent_id=view.parent_id,
        adam_id=view.adam_id,
        instruction=view.instruction,
        child_count=sum(1 for row in (bus.magis_book.list_all() if bus.magis_book else []) if row.parent_id == view.id),
        member_count=len(bus.memberships_book.list_for_magis(magis_id=view.id)) if bus.memberships_book else 0,
        created_at=view.created_at or "",
        updated_at=view.updated_at or "",
    )


def _role_out(view: MagisRoleView) -> RoleOut:
    return RoleOut(
        id=view.id,
        magis_id=view.magis_id,
        name=view.name,
        instruction=view.instruction,
        is_reserved=view.is_reserved,
    )


def _membership_out(bus: Bus, view) -> MembershipOut:
    role = bus.roles_book.get(role_id=view.role_id) if bus.roles_book else None
    return MembershipOut(
        id=view.id,
        magic_id=view.id,
        magic_name=None,
        role_id=view.role_id,
        role_name=role.name if role else "",
    )


def _admin_out(bus: Bus, view) -> MAGISAdminOut:
    contact = bus.contacts_book.get(contact_id=view.uid)
    return MAGISAdminOut(
        id=view.id,
        magis_id=view.magis_id,
        telegram_id=contact.telegram_id if contact and contact.telegram_id is not None else 0,
        display_name=(contact.display_name or contact.name) if contact else None,
    )


def _translate_bus_error(exc: Exception) -> MagiHTTPException:
    """Map bus-side exceptions to MagiHTTPException preserving pre-refactor codes."""
    if isinstance(exc, LookupError):
        return MagiHTTPException(404, "not_found.magis", str(exc))
    if isinstance(exc, PermissionError):
        text = str(exc).lower()
        if "reserved" in text:
            return MagiHTTPException(403, "forbidden.reserved_role", str(exc))
        return MagiHTTPException(403, "forbidden.magis_management_scope", str(exc))
    if isinstance(exc, ValueError):
        text = str(exc).lower()
        if "name" in text and ("duplicate" in text or "exists" in text):
            return MagiHTTPException(400, "validation.magis_name_duplicate", str(exc))
        if "cycle" in text or "own parent" in text:
            return MagiHTTPException(400, "validation.magis_cycle", str(exc))
        if "role name" in text and ("duplicate" in text or "exists" in text):
            return MagiHTTPException(400, "validation.role_name_duplicate", str(exc))
        if "reserved" in text:
            return MagiHTTPException(400, "validation.role_name_reserved", str(exc))
        if "in use" in text or "reassign" in text:
            return MagiHTTPException(409, "validation.role_in_use", str(exc))
        if "already has an adam" in text or "already assigned" in text:
            return MagiHTTPException(409, "validation.adam_already_assigned", str(exc))
        if "one direct magis" in text or "only one" in text:
            return MagiHTTPException(409, "validation.magic_already_assigned", str(exc))
        return MagiHTTPException(400, "validation.invalid_value", str(exc))
    raise exc


# -- Scope checks -------------------------------------------------------


def _served_direct_magis_id(bus: Bus) -> int | None:
    raw = os.environ.get("MAGI_RUNTIME_ID")
    if not raw or not raw.isdigit() or bus.memberships_book is None:
        return None
    membership = bus.memberships_book.get(magi_id=int(raw))
    return membership.magis_id if membership is not None else None


def _require_managed(bus: Bus, magis_id: int) -> None:
    served = _served_direct_magis_id(bus)
    if served is not None and served != magis_id:
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.magis_management_scope",
            detail="MAGIS administration is limited to the current MAGI's direct MAGIS",
        )


def _magis_or_404(bus: Bus, magis_id: int):
    view = bus.magis_book.get(magis_id=magis_id) if bus.magis_book else None
    if view is None:
        raise MagiHTTPException(status_code=404, code="not_found.magis", detail="MAGIS not found")
    return view


# -- Routes -------------------------------------------------------------


@router.get("/magis", response_model=list[MAGISOut])
def list_magis(_admin: AdminGate, bus: BusDep) -> list[MAGISOut]:
    """List the MAGIS row this WebUI's admin scope allows.

    The bus returns all MAGIS rows (with counts populated); the API
    filters to the served MAGIS scope to preserve the pre-refactor
    "single direct MAGIS" model.
    """
    served = _served_direct_magis_id(bus)
    rows = bus.magis_book.list_all() if bus.magis_book else []
    if served is None:
        return [_magis_out(bus, v) for v in rows]
    return [_magis_out(bus, v) for v in rows if v.id == served]


@router.post("/magis", response_model=MAGISOut, status_code=201)
def create_magis(payload: MAGISCreate, _admin: AdminGate, bus: BusDep) -> MAGISOut:
    if payload.parent_id is not None:
        _magis_or_404(bus, payload.parent_id)
        _require_managed(bus, payload.parent_id)
    try:
        view = bus.magis_book.add(
            name=payload.name,
            instruction=payload.instruction,
            parent_id=payload.parent_id,
        )
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.magis", str(exc)) from exc
    except ValueError as exc:
        raise _translate_bus_error(exc) from exc
    # Every new MAGIS has the reserved role vocabulary before a MAGIC can be
    # created in it.  This is an API-level composition step over public Books;
    # membership creation still owns the role/MAGIS invariant itself.
    from magi.bus.library.magis.membershipBook import DEFAULT_ROLE_INSTRUCTIONS

    for role_name in ("ADAM", "EVA"):
        bus.roles_book.add(
            magis_id=view.id,
            name=role_name,
            instruction=DEFAULT_ROLE_INSTRUCTIONS[role_name],
            is_reserved=True,
        )
    return _magis_out(bus, view)


@router.get("/magis/{magis_id}", response_model=MAGISOut)
def get_magis(magis_id: int, _admin: AdminGate, bus: BusDep) -> MAGISOut:
    _require_managed(bus, magis_id)
    return _magis_out(bus, _magis_or_404(bus, magis_id))


@router.patch("/magis/{magis_id}", response_model=MAGISOut)
def update_magis(magis_id: int, payload: MAGISUpdate, _admin: AdminGate, bus: BusDep) -> MAGISOut:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    fields_set = payload.model_fields_set
    kwargs: dict[str, object] = {}
    if "name" in fields_set:
        kwargs["name"] = payload.name
    if "instruction" in fields_set:
        kwargs["instruction"] = payload.instruction
    if "parent_id" in fields_set:
        kwargs["parent_id"] = payload.parent_id
    try:
        view = bus.magis_book.update(magis_id=magis_id, set_parent_id="parent_id" in fields_set, **kwargs)
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _magis_out(bus, view)


@router.delete("/magis/{magis_id}", status_code=204)
def delete_magis(magis_id: int, _admin: AdminGate, bus: BusDep) -> Response:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    bus.magis_book.delete(magis_id=magis_id)
    return Response(status_code=204)


# -- Roles --------------------------------------------------------------


@router.get("/magis/{magis_id}/roles", response_model=list[RoleOut])
def list_roles(magis_id: int, _admin: AdminGate, bus: BusDep) -> list[RoleOut]:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    return [_role_out(v) for v in bus.roles_book.list_for_magis(magis_id=magis_id)]


@router.post("/magis/{magis_id}/roles", response_model=RoleOut, status_code=201)
def create_role(magis_id: int, payload: RoleCreate, _admin: AdminGate, bus: BusDep) -> RoleOut:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    try:
        view = bus.roles_book.add(
            magis_id=magis_id,
            name=payload.name,
            instruction=payload.instruction,
        )
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _role_out(view)


@router.patch("/magis/{magis_id}/roles/{role_id}", response_model=RoleOut)
def update_role(
    magis_id: int,
    role_id: int,
    payload: RoleUpdate,
    _admin: AdminGate, bus: BusDep,
) -> RoleOut:
    _require_managed(bus, magis_id)
    fields_set = payload.model_fields_set
    kwargs: dict[str, object] = {}
    if "name" in fields_set:
        kwargs["name"] = payload.name
    if "instruction" in fields_set:
        kwargs["instruction"] = payload.instruction
    try:
        view = bus.roles_book.update(role_id=role_id, magis_id=magis_id, **kwargs)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _role_out(view)


@router.delete("/magis/{magis_id}/roles/{role_id}", status_code=204)
def delete_role(magis_id: int, role_id: int, _admin: AdminGate, bus: BusDep) -> Response:
    _require_managed(bus, magis_id)
    try:
        deleted = bus.roles_book.delete(role_id=role_id, magis_id=magis_id)
    except (PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    if not deleted:
        raise MagiHTTPException(
            status_code=404,
            code="validation.magis_role_not_found",
            detail="role does not belong to this MAGIS",
        )
    return Response(status_code=204)


# -- Memberships --------------------------------------------------------


@router.get("/magis/{magis_id}/memberships", response_model=list[MembershipOut])
def list_memberships(magis_id: int, _admin: AdminGate, bus: BusDep) -> list[MembershipOut]:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    return [_membership_out(bus, v) for v in bus.memberships_book.list_for_magis(magis_id=magis_id)]


@router.post("/magis/{magis_id}/memberships", response_model=MembershipOut, status_code=201)
def create_membership(
    magis_id: int,
    payload: MembershipCreate,
    _admin: AdminGate, bus: BusDep,
) -> MembershipOut:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    try:
        view = bus.memberships_book.add(
            magis_id=magis_id,
            role_id=payload.role_id,
        )
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _membership_out(bus, view)


@router.patch("/magis/{magis_id}/memberships/{membership_id}", response_model=MembershipOut)
def update_membership(
    magis_id: int,
    membership_id: int,
    payload: MembershipUpdate,
    _admin: AdminGate, bus: BusDep,
) -> MembershipOut:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    try:
        view = bus.memberships_book.update_role(magi_id=membership_id, magis_id=magis_id, role_id=payload.role_id)
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _membership_out(bus, view)


@router.delete("/magis/{magis_id}/memberships/{membership_id}", status_code=204)
def delete_membership(
    magis_id: int,
    membership_id: int,
    _admin: AdminGate, bus: BusDep,
) -> Response:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    try:
        member = bus.memberships_book.get(magi_id=membership_id)
        deleted = bool(member and member.magis_id == magis_id and bus.memberships_book.remove(magi_id=membership_id))
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.membership", str(exc)) from exc
    if not deleted:
        raise MagiHTTPException(404, "not_found.membership", "membership not found")
    return Response(status_code=204)


# -- Admins -------------------------------------------------------------


@router.get("/magis/{magis_id}/admins", response_model=list[MAGISAdminOut])
def list_magis_admins(magis_id: int, _admin: AdminGate, bus: BusDep) -> list[MAGISAdminOut]:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    return [_admin_out(bus, v) for v in bus.magis_admins_book.list_for_magis(magis_id=magis_id)]


@router.post("/magis/{magis_id}/admins", response_model=MAGISAdminOut, status_code=201)
def add_magis_admin(
    magis_id: int,
    payload: MAGISAdminCreate,
    _admin: AdminGate, bus: BusDep,
) -> MAGISAdminOut:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    contact = bus.contacts_book.get_by_telegram(telegram_id=payload.telegram_id)
    if contact is None:
        raise MagiHTTPException(404, "not_found.contact", "Telegram account is not a local contact")
    try:
        view = bus.magis_admins_book.add(uid=contact.id, magis_id=magis_id)
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.magis", str(exc)) from exc
    return _admin_out(bus, view)


@router.delete("/magis/{magis_id}/admins/{admin_id}", status_code=204)
def delete_magis_admin(
    magis_id: int,
    admin_id: int,
    _admin: AdminGate, bus: BusDep,
) -> Response:
    _magis_or_404(bus, magis_id); _require_managed(bus, magis_id)
    deleted = bus.magis_admins_book.remove_by_id(admin_id=admin_id, magis_id=magis_id)
    if not deleted:
        raise MagiHTTPException(404, "not_found.magis_admin", "MAGIS admin not found")
    return Response(status_code=204)
