"""MAGIS tree, team instruction, role, and membership APIs.

All data access goes through the bus facade — no ``magi.db.*`` imports
(``channels → db`` boundary enforced by
``tests/architecture/test_import_boundaries.py``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from magi.bus import get_bus
from magi.bus.jobs.protocols.magis import (
    MagisAdminView,
    MagisMembershipView,
    MagisRoleView,
    MagisView,
)
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


# -- Conversion helpers -------------------------------------------------


def _bus():
    return get_bus()


def _magis_out(view: MagisView) -> MAGISOut:
    return MAGISOut(
        id=view.id,
        name=view.name,
        parent_id=view.parent_id,
        adam_id=view.adam_id,
        instruction=view.instruction,
        child_count=len(view.child_ids),
        member_count=view.member_count,
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


def _membership_out(view: MagisMembershipView) -> MembershipOut:
    return MembershipOut(
        id=view.id,
        magic_id=view.magic_id,
        magic_name=view.magic_name,
        role_id=view.role_id,
        role_name=view.role_name,
    )


def _admin_out(view: MagisAdminView) -> MAGISAdminOut:
    return MAGISAdminOut(
        id=view.id,
        magis_id=view.group_id,
        telegram_id=view.magic_id,
        display_name=view.display_name,
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


def _served_direct_magis_id() -> int | None:
    return _bus().magis.served_direct_magis_id()


def _require_managed(magis_id: int) -> None:
    served = _served_direct_magis_id()
    if served != magis_id:
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.magis_management_scope",
            detail="MAGIS administration is limited to the current MAGI's direct MAGIS",
        )


def _magis_or_404(magis_id: int) -> MagisView:
    view = _bus().magis.get_magis(magis_id)
    if view is None:
        raise MagiHTTPException(status_code=404, code="not_found.magis", detail="MAGIS not found")
    return view


# -- Routes -------------------------------------------------------------


@router.get("/magis", response_model=list[MAGISOut])
def list_magis(_admin: AdminGate) -> list[MAGISOut]:
    """List the MAGIS row this WebUI's admin scope allows.

    The bus returns all MAGIS rows (with counts populated); the API
    filters to the served MAGIS scope to preserve the pre-refactor
    "single direct MAGIS" model.
    """
    served = _served_direct_magis_id()
    rows = _bus().magis.list_magis()
    if served is None:
        return [_magis_out(v) for v in rows]
    return [_magis_out(v) for v in rows if v.id == served]


@router.post("/magis", response_model=MAGISOut, status_code=201)
def create_magis(payload: MAGISCreate, _admin: AdminGate) -> MAGISOut:
    bus = _bus()
    if payload.parent_id is not None:
        _magis_or_404(payload.parent_id)
        _require_managed(payload.parent_id)
    try:
        view = bus.magis.create_magis(
            name=payload.name,
            instruction=payload.instruction,
            parent_id=payload.parent_id,
        )
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.magis", str(exc)) from exc
    except ValueError as exc:
        raise _translate_bus_error(exc) from exc
    # Provisioning is control-plane work.  A MAGI never gets
    # Kubernetes API credentials just because it manages a MAGIS.
    # Phase 2 — routed through the BUS dispatcher (plan §4.5: API
    # publishes BUS commands, never calls orchestrator directly).
    try:
        from magi.bus.jobs.services.runtime import OrchestratorUnavailable

        bus = get_bus()
        bus.runtime.provision_magis(magis_id=view.id, magis_name=view.name)
    except OrchestratorUnavailable as exc:
        raise MagiHTTPException(
            503, "runtime.magis_provisioning_unavailable", str(exc)
        ) from exc
    except Exception as exc:
        raise MagiHTTPException(
            503, "runtime.magis_provisioning_unavailable", str(exc)
        ) from exc
    # Refresh so child_ids appears in the response.
    refreshed = bus.magis.get_magis(view.id)
    return _magis_out(refreshed or view)


@router.get("/magis/{magis_id}", response_model=MAGISOut)
def get_magis(magis_id: int, _admin: AdminGate) -> MAGISOut:
    view = _magis_or_404(magis_id)
    return _magis_out(view)


@router.patch("/magis/{magis_id}", response_model=MAGISOut)
def update_magis(magis_id: int, payload: MAGISUpdate, _admin: AdminGate) -> MAGISOut:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    fields_set = payload.model_fields_set
    kwargs: dict[str, object] = {}
    if "name" in fields_set:
        kwargs["name"] = payload.name
    if "instruction" in fields_set:
        kwargs["instruction"] = payload.instruction
    if "parent_id" in fields_set:
        kwargs["parent_id"] = payload.parent_id
    try:
        view = _bus().magis.update_magis(magis_id, **kwargs)
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _magis_out(view)


@router.delete("/magis/{magis_id}", status_code=204)
def delete_magis(magis_id: int, _admin: AdminGate) -> Response:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    _bus().magis.delete_magis(magis_id)
    return Response(status_code=204)


# -- Roles --------------------------------------------------------------


@router.get("/magis/{magis_id}/roles", response_model=list[RoleOut])
def list_roles(magis_id: int, _admin: AdminGate) -> list[RoleOut]:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    return [_role_out(v) for v in _bus().magis.list_roles_in_magis(magis_id)]


@router.post("/magis/{magis_id}/roles", response_model=RoleOut, status_code=201)
def create_role(magis_id: int, payload: RoleCreate, _admin: AdminGate) -> RoleOut:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    try:
        view = _bus().magis.create_role_in_magis(
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
    _admin: AdminGate,
) -> RoleOut:
    _require_managed(magis_id)
    fields_set = payload.model_fields_set
    kwargs: dict[str, object] = {}
    if "name" in fields_set:
        kwargs["name"] = payload.name
    if "instruction" in fields_set:
        kwargs["instruction"] = payload.instruction
    try:
        view = _bus().magis.update_role_in_magis(magis_id, role_id, **kwargs)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _role_out(view)


@router.delete("/magis/{magis_id}/roles/{role_id}", status_code=204)
def delete_role(magis_id: int, role_id: int, _admin: AdminGate) -> Response:
    _require_managed(magis_id)
    try:
        deleted = _bus().magis.delete_role_in_magis(magis_id, role_id)
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
def list_memberships(magis_id: int, _admin: AdminGate) -> list[MembershipOut]:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    return [_membership_out(v) for v in _bus().magis.list_memberships(magis_id)]


@router.post("/magis/{magis_id}/memberships", response_model=MembershipOut, status_code=201)
def create_membership(
    magis_id: int,
    payload: MembershipCreate,
    _admin: AdminGate,
) -> MembershipOut:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    try:
        view = _bus().magis.create_membership_in_magis(
            magis_id=magis_id,
            magic_id=payload.magic_id,
            role_id=payload.role_id,
        )
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _membership_out(view)


@router.patch("/magis/{magis_id}/memberships/{membership_id}", response_model=MembershipOut)
def update_membership(
    magis_id: int,
    membership_id: int,
    payload: MembershipUpdate,
    _admin: AdminGate,
) -> MembershipOut:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    try:
        view = _bus().magis.update_membership_role_in_magis(
            magis_id=magis_id,
            membership_id=membership_id,
            new_role_id=payload.role_id,
        )
    except (LookupError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _membership_out(view)


@router.delete("/magis/{magis_id}/memberships/{membership_id}", status_code=204)
def delete_membership(
    magis_id: int,
    membership_id: int,
    _admin: AdminGate,
) -> Response:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    try:
        deleted = _bus().magis.delete_membership_in_magis(
            magis_id=magis_id, membership_id=membership_id
        )
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.membership", str(exc)) from exc
    if not deleted:
        raise MagiHTTPException(404, "not_found.membership", "membership not found")
    return Response(status_code=204)


# -- Admins -------------------------------------------------------------


@router.get("/magis/{magis_id}/admins", response_model=list[MAGISAdminOut])
def list_magis_admins(magis_id: int, _admin: AdminGate) -> list[MAGISAdminOut]:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    return [_admin_out(v) for v in _bus().magis.list_admins(magis_id)]


@router.post("/magis/{magis_id}/admins", response_model=MAGISAdminOut, status_code=201)
def add_magis_admin(
    magis_id: int,
    payload: MAGISAdminCreate,
    _admin: AdminGate,
) -> MAGISAdminOut:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    try:
        view = _bus().magis.add_admin_with_display(
            group_id=magis_id,
            telegram_id=payload.telegram_id,
            display_name=payload.display_name,
        )
    except LookupError as exc:
        raise MagiHTTPException(404, "not_found.magis", str(exc)) from exc
    return _admin_out(view)


@router.delete("/magis/{magis_id}/admins/{admin_id}", status_code=204)
def delete_magis_admin(
    magis_id: int,
    admin_id: int,
    _admin: AdminGate,
) -> Response:
    _magis_or_404(magis_id)
    _require_managed(magis_id)
    deleted = _bus().magis.delete_admin_in_magis(magis_id, admin_id)
    if not deleted:
        raise MagiHTTPException(404, "not_found.magis_admin", "MAGIS admin not found")
    return Response(status_code=204)
