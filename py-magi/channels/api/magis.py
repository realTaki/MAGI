"""MAGIS tree, team instruction, role, and membership APIs.

All data access goes through the bus facade — no ``magi.db.*`` imports
(``channels → db`` boundary enforced by
``tests/architecture/test_import_boundaries.py``).
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from old_bus import Bus
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException

if TYPE_CHECKING:
    from old_bus.firmwares.books.magis.magisBook import (
        Magis,
        MagisAdmin,
        MagisAdminBook,
        MagisBook,
    )
    from old_bus.firmwares.books.magis.membershipBook import (
        MagisMembership,
        MagisMembershipBook,
        MagisRole,
        MagisRoleBook,
    )

router = APIRouter(tags=["magis"])


def _admin_gate(request: Request) -> str:
    from channels.api.auth_gates import admin_gate

    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


def _require_book[BookT](book: BookT | None, name: str) -> BookT:
    """Return *book*, or 503 when the MAGIS database isn't attached.

    All four MAGIS-side Books on the bus are ``| None`` — they're only
    populated when a MAGIS database is configured. Every route in this
    module is meaningless without them, so a missing Book is a
    deployment-state error (503), not an ``AttributeError`` on ``None``.
    """
    if book is None:
        raise MagiHTTPException(
            status_code=503,
            code="unavailable.magis_store",
            detail=f"MAGIS store '{name}' is not available on this node",
        )
    return book


def _magis_book(bus: Bus) -> MagisBook:
    return _require_book(bus.magis_book, "magis_book")


def _roles_book(bus: Bus) -> MagisRoleBook:
    return _require_book(bus.roles_book, "roles_book")


def _memberships_book(bus: Bus) -> MagisMembershipBook:
    return _require_book(bus.memberships_book, "memberships_book")


def _admins_book(bus: Bus) -> MagisAdminBook:
    return _require_book(bus.magis_admins_book, "magis_admins_book")


# -- Pydantic response models (no ORM imports) -------------------------


class MAGISOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    adam_id: int | None
    instruction: str
    child_count: int = 0
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


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
    magi_id: int
    magi_name: str | None = None
    role_id: int
    role_name: str
    responsibility: str


class MembershipCreate(BaseModel):
    """Create a new MAGI identity in this MAGIS.

    ``magis_memberships.id`` *is* the MAGI id.  There is no separate
    MAGI record that can be attached later, so accepting ``magi_id`` here
    would either be impossible or silently ignored.  Reject unknown fields to
    make that model boundary visible to API clients.
    """

    model_config = ConfigDict(extra="forbid")
    role_id: int = Field(ge=1)
    responsibility: str = Field(default="", max_length=12000)


class MembershipUpdate(BaseModel):
    role_id: int | None = Field(default=None, ge=1)
    responsibility: str | None = Field(default=None, max_length=12000)


class MAGISAdminOut(BaseModel):
    id: int
    magis_id: int
    name: str
    tgid: int | None = None
    auth_mode: str


class MAGISAdminCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tgid: int | None = None


# -- Conversion helpers -------------------------------------------------


def _magis_out(bus: Bus, view: Magis) -> MAGISOut:
    return MAGISOut(
        id=view.id,
        name=view.name,
        parent_id=view.parent_id,
        adam_id=view.adam_id,
        instruction=view.instruction,
        child_count=sum(
            1
            for row in (bus.magis_book.list_all() if bus.magis_book else [])
            if row.parent_id == view.id
        ),
        member_count=len(bus.memberships_book.list_for_magis(magis_id=view.id))
        if bus.memberships_book
        else 0,
        created_at=cast(datetime, view.created_at),
        updated_at=cast(datetime, view.updated_at),
    )


def _role_out(view: MagisRole) -> RoleOut:
    return RoleOut(
        id=view.id,
        magis_id=view.magis_id,
        name=view.name,
        instruction=view.instruction,
        is_reserved=view.is_reserved,
    )


def _membership_out(bus: Bus, view: MagisMembership) -> MembershipOut:
    role = bus.roles_book.get(view.role_id) if bus.roles_book else None
    return MembershipOut(
        id=view.id,
        magi_id=view.id,
        magi_name=None,
        role_id=view.role_id,
        role_name=role.name if role else "",
        responsibility=view.responsibility,
    )


def _admin_out(_bus: Bus, view: MagisAdmin) -> MAGISAdminOut:
    return MAGISAdminOut(
        id=view.id,
        magis_id=view.magis_id,
        name=view.name,
        tgid=view.tgid,
        auth_mode=view.auth_mode,
    )


def _two_factor_is_enabled(request: Request, bus: Bus) -> bool:
    """Read the caller posture solely for additional-admin creation."""
    from channels.api.proxy_auth import verified_proxy_scope

    scope = verified_proxy_scope(bus, request)
    if scope is not None:
        is_admin, _assigned, two_factor, _admin_id = scope
        return is_admin and two_factor
    from channels.api.auth import resolve_session

    session = resolve_session(bus, request.cookies.get("magi_session"))
    return bool(session and session.get("admin") and session.get("two_factor"))


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
            return MagiHTTPException(409, "validation.magi_already_assigned", str(exc))
        return MagiHTTPException(400, "validation.invalid_value", str(exc))
    raise exc


# -- Scope checks -------------------------------------------------------


def _served_direct_magis_id(bus: Bus) -> int | None:
    raw = os.environ.get("MAGI_RUNTIME_ID")
    if not raw or not raw.isdigit() or bus.memberships_book is None:
        return None
    membership = bus.memberships_book.get(int(raw))
    return membership.magis_id if membership is not None else None


def _require_managed(bus: Bus, magis_id: int) -> None:
    served = _served_direct_magis_id(bus)
    if served is not None and served != magis_id:
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.magis_management_scope",
            detail="MAGIS administration is limited to the current MAGI's direct MAGIS",
        )


def _magis_or_404(bus: Bus, magis_id: int) -> Magis:
    view = _magis_book(bus).get(magis_id)
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
    rows = _magis_book(bus).list_all()
    if served is None:
        return [_magis_out(bus, v) for v in rows]
    return [_magis_out(bus, v) for v in rows if v.id == served]


@router.post("/magis", response_model=MAGISOut, status_code=201)
def create_magis(payload: MAGISCreate, _admin: AdminGate, bus: BusDep) -> MAGISOut:
    if payload.parent_id is not None:
        _magis_or_404(bus, payload.parent_id)
        _require_managed(bus, payload.parent_id)
    try:
        from old_bus.firmwares.books.magis.magisBook import Magis

        magis_id = _magis_book(bus).add(Magis(
            name=payload.name,
            instruction=payload.instruction,
            parent_id=payload.parent_id,
        ))
        view = _magis_book(bus).get(magis_id)
        if view is None:
            raise RuntimeError(f"MAGIS row {magis_id} disappeared after insert")
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.magis", str(exc)) from exc
    except ValueError as exc:
        raise _translate_bus_error(exc) from exc
    # Every new MAGIS has the reserved role vocabulary before a MAGI can be
    # created in it.  This is an API-level composition step over public Books;
    # membership creation still owns the role/MAGIS invariant itself.
    from old_bus.firmwares.books.magis.membershipBook import DEFAULT_ROLE_INSTRUCTIONS

    for role_name in ("ADAM", "EVA"):
        from old_bus.firmwares.books.magis.membershipBook import MagisRole

        _roles_book(bus).add(MagisRole(
            magis_id=view.id,
            name=role_name,
            instruction=DEFAULT_ROLE_INSTRUCTIONS[role_name],
            is_reserved=True,
        ))
    return _magis_out(bus, view)


@router.get("/magis/{magis_id}", response_model=MAGISOut)
def get_magis(magis_id: int, _admin: AdminGate, bus: BusDep) -> MAGISOut:
    _require_managed(bus, magis_id)
    return _magis_out(bus, _magis_or_404(bus, magis_id))


@router.patch("/magis/{magis_id}", response_model=MAGISOut)
def update_magis(magis_id: int, payload: MAGISUpdate, _admin: AdminGate, bus: BusDep) -> MAGISOut:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    fields_set = payload.model_fields_set
    kwargs: dict[str, Any] = {}
    if "name" in fields_set:
        kwargs["name"] = payload.name
    if "instruction" in fields_set:
        kwargs["instruction"] = payload.instruction
    if "parent_id" in fields_set:
        kwargs["parent_id"] = payload.parent_id
    current = _magis_book(bus).get(magis_id)
    if current is None:
        raise MagiHTTPException(status_code=404, code="not_found.magis", detail="MAGIS not found")
    try:
        view = replace(current, **kwargs)
        if not _magis_book(bus).update(view):
            raise MagiHTTPException(status_code=404, code="not_found.magis", detail="MAGIS not found")
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _magis_out(bus, view)


@router.delete("/magis/{magis_id}", status_code=204)
def delete_magis(magis_id: int, _admin: AdminGate, bus: BusDep) -> Response:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    _magis_book(bus).delete(magis_id)
    return Response(status_code=204)


# -- Roles --------------------------------------------------------------


@router.get("/magis/{magis_id}/roles", response_model=list[RoleOut])
def list_roles(magis_id: int, _admin: AdminGate, bus: BusDep) -> list[RoleOut]:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    return [_role_out(v) for v in _roles_book(bus).list_for_magis(magis_id=magis_id)]


@router.post("/magis/{magis_id}/roles", response_model=RoleOut, status_code=201)
def create_role(magis_id: int, payload: RoleCreate, _admin: AdminGate, bus: BusDep) -> RoleOut:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    try:
        from old_bus.firmwares.books.magis.membershipBook import MagisRole

        role_id = _roles_book(bus).add(MagisRole(
            magis_id=magis_id,
            name=payload.name,
            instruction=payload.instruction,
        ))
        view = _roles_book(bus).get(role_id)
        if view is None:
            raise RuntimeError(f"role row {role_id} disappeared after insert")
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _role_out(view)


@router.patch("/magis/{magis_id}/roles/{role_id}", response_model=RoleOut)
def update_role(
    magis_id: int,
    role_id: int,
    payload: RoleUpdate,
    _admin: AdminGate,
    bus: BusDep,
) -> RoleOut:
    _require_managed(bus, magis_id)
    fields_set = payload.model_fields_set
    kwargs: dict[str, Any] = {}
    if "name" in fields_set:
        kwargs["name"] = payload.name
    if "instruction" in fields_set:
        kwargs["instruction"] = payload.instruction
    current = _roles_book(bus).get(role_id)
    if current is None or current.magis_id != magis_id:
        raise MagiHTTPException(
            status_code=404,
            code="validation.magis_role_not_found",
            detail="role does not belong to this MAGIS",
        )
    try:
        view = replace(current, **kwargs)
        if not _roles_book(bus).update(view):
            raise MagiHTTPException(
                status_code=404,
                code="validation.magis_role_not_found",
                detail="role does not belong to this MAGIS",
            )
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _role_out(view)


@router.delete("/magis/{magis_id}/roles/{role_id}", status_code=204)
def delete_role(magis_id: int, role_id: int, _admin: AdminGate, bus: BusDep) -> Response:
    _require_managed(bus, magis_id)
    try:
        role = _roles_book(bus).get(role_id)
        deleted = role is not None and role.magis_id == magis_id and _roles_book(bus).delete(role_id)
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
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    return [
        _membership_out(bus, v) for v in _memberships_book(bus).list_for_magis(magis_id=magis_id)
    ]


@router.post("/magis/{magis_id}/memberships", response_model=MembershipOut, status_code=201)
def create_membership(
    magis_id: int,
    payload: MembershipCreate,
    _admin: AdminGate,
    bus: BusDep,
) -> MembershipOut:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    try:
        from old_bus.firmwares.books.magis.membershipBook import MagisMembership

        membership_id = _memberships_book(bus).add(MagisMembership(
            magis_id=magis_id,
            role_id=payload.role_id,
            responsibility=payload.responsibility,
        ))
        view = _memberships_book(bus).get(membership_id)
        if view is None:
            raise RuntimeError(f"membership row {membership_id} disappeared after insert")
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _membership_out(bus, view)


@router.patch("/magis/{magis_id}/memberships/{membership_id}", response_model=MembershipOut)
def update_membership(
    magis_id: int,
    membership_id: int,
    payload: MembershipUpdate,
    _admin: AdminGate,
    bus: BusDep,
) -> MembershipOut:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    try:
        if payload.role_id is not None:
            view = _memberships_book(bus).update_role(
                magi_id=membership_id, magis_id=magis_id, role_id=payload.role_id
            )
        else:
            view = _memberships_book(bus).get(membership_id)
            if view is not None and view.magis_id != magis_id:
                view = None
        if view is not None and payload.responsibility is not None:
            view = _memberships_book(bus).update_responsibility(
                magi_id=membership_id,
                magis_id=magis_id,
                responsibility=payload.responsibility,
            )
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    if view is None:
        raise MagiHTTPException(404, "not_found.membership", "membership not found")
    return _membership_out(bus, view)


@router.delete("/magis/{magis_id}/memberships/{membership_id}", status_code=204)
def delete_membership(
    magis_id: int,
    membership_id: int,
    _admin: AdminGate,
    bus: BusDep,
) -> Response:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    try:
        member = _memberships_book(bus).get(membership_id)
        deleted = bool(
            member
            and member.magis_id == magis_id
            and _memberships_book(bus).remove(magi_id=membership_id)
        )
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.membership", str(exc)) from exc
    if not deleted:
        raise MagiHTTPException(404, "not_found.membership", "membership not found")
    return Response(status_code=204)


# -- Admins -------------------------------------------------------------


@router.get("/magis/{magis_id}/admins", response_model=list[MAGISAdminOut])
def list_magis_admins(magis_id: int, _admin: AdminGate, bus: BusDep) -> list[MAGISAdminOut]:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    return [_admin_out(bus, v) for v in _admins_book(bus).list_for_magis(magis_id=magis_id)]


@router.post("/magis/{magis_id}/admins", response_model=MAGISAdminOut, status_code=201)
def add_magis_admin(
    magis_id: int,
    payload: MAGISAdminCreate,
    _admin: AdminGate,
    bus: BusDep,
    request: Request,
) -> MAGISAdminOut:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    if not _two_factor_is_enabled(request, bus):
        raise MagiHTTPException(
            403,
            "auth.two_factor_required_for_user_creation",
            "Enable IM two-factor verification before adding a MAGIS administrator",
        )
    try:
        from old_bus.firmwares.books.magis.magisBook import MagisAdmin

        admin_id = _admins_book(bus).add(MagisAdmin(
            magis_id=magis_id,
            name=payload.name,
            tgid=payload.tgid,
        ))
        view = _admins_book(bus).get(admin_id)
        if view is None:
            raise RuntimeError(f"MAGIS admin row {admin_id} disappeared after insert")
        # This endpoint runs in the MAGI whose local data the Settings page
        # manages.  The projection is local ownership only; it is not the
        # source of the just-created MAGIS authority.
        bus.contacts_book.ensure_magis_admin_projection(
            magis_admin_id=view.id,
            display_name=view.name,
        )
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _admin_out(bus, view)


@router.delete("/magis/{magis_id}/admins/{admin_id}", status_code=204)
def delete_magis_admin(
    magis_id: int,
    admin_id: int,
    _admin: AdminGate,
    bus: BusDep,
) -> Response:
    _magis_or_404(bus, magis_id)
    _require_managed(bus, magis_id)
    deleted = _admins_book(bus).remove_by_id(admin_id=admin_id, magis_id=magis_id)
    if not deleted:
        raise MagiHTTPException(404, "not_found.magis_admin", "MAGIS admin not found")
    return Response(status_code=204)
