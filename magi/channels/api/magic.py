"""MAGI CRUD and lifecycle API.

A MAGI is created independently.  MAGIS membership and role assignment live
in the MAGIS API, so creation does not imply either membership or a running
container.

All data access goes through the bus facade — no ``magi.db.*`` imports
(``channels → db`` boundary enforced by
``tests/architecture/test_import_boundaries.py``).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from magi.bus import get_bus
from magi.bus.contracts.magis import (
    EvaRuntimeView,
    MagicView,
    MembershipBrief as MembershipBriefDTO,
)
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magic")
router = APIRouter(tags=["magic"])


def _admin_gate(request: Request) -> str:
    from magi.channels.api.auth_gates import admin_gate
    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


# -- Pydantic response models (no ORM imports) -------------------------


class MembershipBrief(BaseModel):
    magis_id: int
    magis_name: str
    role_id: int
    role_name: str


class EvaRuntimeOut(BaseModel):
    desired_state: str
    observed_state: str
    namespace: str | None = None
    deployment_name: str | None = None
    workspace_claim_name: str | None = None
    credential_secret_name: str | None = None
    last_error: str | None = None
    updated_at: str


class MAGICOut(BaseModel):
    id: int
    name: str | None = None
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    memberships: list[MembershipBrief] = []
    runtime: EvaRuntimeOut | None = None
    created_at: str
    updated_at: str


class MAGICCreate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


class MAGICUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


class InstructionPayload(BaseModel):
    instruction: str = Field(max_length=12000)


class InstructionOut(BaseModel):
    magic_id: int
    instruction: str


# -- Conversion helpers -------------------------------------------------


def _bus():
    return get_bus()


def _runtime_out(view: EvaRuntimeView | None) -> EvaRuntimeOut | None:
    if view is None:
        return None
    return EvaRuntimeOut(
        desired_state=view.desired_state,
        observed_state=view.observed_state,
        namespace=view.namespace,
        deployment_name=view.deployment_name,
        workspace_claim_name=view.workspace_claim_name,
        credential_secret_name=view.credential_secret_name,
        last_error=view.last_error,
        updated_at=view.updated_at,
    )


def _membership_brief(dto: MembershipBriefDTO) -> MembershipBrief:
    return MembershipBrief(
        magis_id=dto.magis_id,
        magis_name=dto.magis_name,
        role_id=dto.role_id,
        role_name=dto.role_name,
    )


def _magic_out(view: MagicView) -> MAGICOut:
    return MAGICOut(
        id=view.id,
        name=view.name,
        provider=view.provider,
        api_key_set=view.api_key_set,
        api_key_last4=view.api_key_last4,
        memberships=[_membership_brief(m) for m in view.memberships],
        runtime=_runtime_out(view.runtime),
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


# -- Error translation --------------------------------------------------


def _translate_bus_error(exc: Exception) -> MagiHTTPException:
    """Map exceptions raised by the bus services to MagiHTTPException.

    The frontend keys off the stable ``code`` field for i18n, so we
    preserve the pre-refactor codes (``not_found.magic``,
    ``runtime.current_magic_protected``,
    ``validation.magic_membership_required``,
    ``validation.eva_provider_credentials_required``,
    ``runtime.orchestrator_unavailable``).

    Anything we can't classify falls back to a 400 with the bus's
    detail so a future error type stays visible to the operator.
    """
    from magi.bus.services.runtime import OrchestratorUnavailable

    if isinstance(exc, OrchestratorUnavailable):
        return MagiHTTPException(
            503, "runtime.orchestrator_unavailable", str(exc)
        )
    if isinstance(exc, LookupError):
        return MagiHTTPException(404, "not_found.magic", str(exc))
    if isinstance(exc, PermissionError):
        return MagiHTTPException(
            409, "runtime.current_magic_protected", str(exc)
        )
    if isinstance(exc, ValueError):
        text = str(exc).lower()
        if "magis" in text and ("membership" in text or "assign" in text):
            code = "validation.magic_membership_required"
        elif "provider" in text or "credential" in text or "api key" in text:
            code = "validation.eva_provider_credentials_required"
        elif "desired_state" in text:
            code = "validation.invalid_value"
        else:
            code = "validation.invalid_value"
        return MagiHTTPException(400, code, str(exc))
    raise exc


# -- Direct / served identity + visibility ------------------------------


def _served_direct_magis_id() -> int | None:
    return _bus().magis.served_direct_magis_id()


def _is_visible_magic(magic_id: int, *, allow_unassigned: bool) -> bool:
    """A MAGIC is visible when it's directly bound to the served MAGIS,
    or it's unassigned and ``allow_unassigned`` is True.

    Mirrors ``_require_visible_magic`` from the pre-refactor
    ``api/magic.py`` — visible == outside the management scope block.
    """
    served = _served_direct_magis_id()
    if served is None:
        return False
    bindings = _bus().magic.list_memberships(magic_id)
    if not bindings:
        return allow_unassigned
    return any(b.magis_id == served for b in bindings)


def _require_visible_magic(magic_id: int, *, allow_unassigned: bool) -> None:
    if not _is_visible_magic(magic_id, allow_unassigned=allow_unassigned):
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.magic_management_scope",
            detail="MAGI is outside the current direct MAGIS",
        )


def _current_magic_id() -> int:
    """Return the MAGI id served by this WebUI container, raising 404 when none."""
    magic_id = _bus().magic.current_runtime_magic_id()
    if magic_id is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.current_magic",
            detail="this runtime is not bound to a MAGI",
        )
    return magic_id


def _magic_or_404(magic_id: int) -> MagicView:
    view = _bus().magic.get_magic(magic_id)
    if view is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.magic", detail="MAGI not found"
        )
    return view


# -- Routes -------------------------------------------------------------


@router.get("/magic", response_model=list[MAGICOut])
def list_magic(_admin: AdminGate) -> list[MAGICOut]:
    bus = _bus()
    served = _served_direct_magis_id()
    direct_ids: set[int] = set()
    if served is not None:
        # MAGIs bound to the served MAGIS — visible regardless of
        # whether they're bound elsewhere too.
        direct_ids = {
            m.magic_id for m in bus.magis.list_memberships(served)
        }
    assigned_ids = bus.magis.assigned_magic_ids()
    views = bus.magic.list_magic(
        direct_ids=direct_ids,
        assigned_ids=assigned_ids,
    )
    return [_magic_out(v) for v in views]


@router.post("/magic", response_model=MAGICOut, status_code=201)
def create_magic(payload: MAGICCreate, _admin: AdminGate) -> MAGICOut:
    view = _bus().magic.create_magic(
        name=payload.name,
        provider=payload.provider,
        api_key=payload.api_key,
    )
    return _magic_out(view)


@router.get("/magic/{magic_id}", response_model=MAGICOut)
def get_magic(magic_id: int, _admin: AdminGate) -> MAGICOut:
    _require_visible_magic(magic_id, allow_unassigned=True)
    return _magic_out(_magic_or_404(magic_id))


@router.patch("/magic/{magic_id}", response_model=MAGICOut)
def update_magic(magic_id: int, payload: MAGICUpdate, _admin: AdminGate) -> MAGICOut:
    _require_visible_magic(magic_id, allow_unassigned=True)
    # Forward only the fields the caller explicitly included; the bus
    # treats anything else as ``_FIELD_UNSET`` and leaves the column
    # untouched.  ``api_key=None`` is forwarded verbatim so the user
    # can clear a stored credential via PATCH.
    update_kwargs: dict[str, object] = {}
    fields_set = payload.model_fields_set
    for field in ("name", "provider", "api_key"):
        if field in fields_set:
            update_kwargs[field] = getattr(payload, field)
    try:
        view = _bus().magic.update_magic(magic_id, **update_kwargs)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _magic_out(view)


@router.get("/magic/self/instruction", response_model=InstructionOut)
def get_self_instruction(_admin: AdminGate) -> InstructionOut:
    magic_id = _current_magic_id()
    personal, _bindings = _bus().magic.instruction_context()
    return InstructionOut(magic_id=magic_id, instruction=personal)


@router.put("/magic/self/instruction", response_model=InstructionOut)
def put_self_instruction(payload: InstructionPayload, _admin: AdminGate) -> InstructionOut:
    magic_id = _current_magic_id()
    try:
        _bus().magic.set_instruction(magic_id, payload.instruction)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return InstructionOut(magic_id=magic_id, instruction=payload.instruction)


@router.get("/magic/{magic_id}/runtime", response_model=EvaRuntimeOut)
def get_runtime(magic_id: int, _admin: AdminGate) -> EvaRuntimeOut:
    _require_visible_magic(magic_id, allow_unassigned=False)
    try:
        view = _bus().magic.ensure_runtime(magic_id)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _runtime_out(view)  # type: ignore[return-value]


def _lifecycle(action: str, magic_id: int) -> EvaRuntimeOut:
    _require_visible_magic(magic_id, allow_unassigned=False)
    bus = _bus()
    current = _current_magic_id()
    if magic_id == current:
        raise MagiHTTPException(
            status_code=409,
            code="runtime.current_magic_protected",
            detail="Cannot stop or restart the MAGI currently serving this session",
        )
    try:
        bus.magic.set_runtime(
            magic_id,
            "running" if action == "start" else "stopped",
            lifecycle_action=action,
        )
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    try:
        view = bus.magic.ensure_runtime(magic_id)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return _runtime_out(view)  # type: ignore[return-value]


@router.post("/magic/{magic_id}/runtime/start", response_model=EvaRuntimeOut)
def start_runtime(magic_id: int, _admin: AdminGate) -> EvaRuntimeOut:
    return _lifecycle("start", magic_id)


@router.post("/magic/{magic_id}/runtime/stop", response_model=EvaRuntimeOut)
def stop_runtime(magic_id: int, _admin: AdminGate) -> EvaRuntimeOut:
    return _lifecycle("stop", magic_id)


@router.delete("/magic/{magic_id}", status_code=204)
def delete_magic(magic_id: int, _admin: AdminGate) -> Response:
    _require_visible_magic(magic_id, allow_unassigned=True)
    current = _current_magic_id()
    if magic_id == current:
        raise MagiHTTPException(
            status_code=409,
            code="runtime.current_magic_protected",
            detail="Cannot delete the MAGI currently serving this session",
        )
    try:
        _bus().magic.delete_magic(magic_id)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_bus_error(exc) from exc
    return Response(status_code=204)
