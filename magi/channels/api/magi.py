"""Per-MAGI identity, runtime-status, and self-instruction APIs.

A MAGI is the identity of a :class:`MagisMembership` row (no separate
``magic`` table anymore — the term was retired when ``magic`` collapsed
into ``magis_memberships``).  The control-plane runtime record adds the
operator-facing name and lifecycle intent; each running MAGI keeps
its personal instruction in its own ``settings_book``.

The module intentionally exposes two routers.  Management routes are mounted
only by the control WebUI, while the self-instruction route is mounted only by
private runtimes.  That keeps a control process from reading or writing a
different MAGI's node-local settings.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.bus.library.magis.runtimeBook import RuntimeDesiredState
from magi.channels.api.auth_gates import admin_gate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

router = APIRouter(tags=["magi"])
self_router = APIRouter(tags=["magi"])
AdminGate = Annotated[str, Depends(admin_gate)]


class MembershipBrief(BaseModel):
    magis_id: int
    magis_name: str
    role_id: int
    role_name: str


class RuntimeOut(BaseModel):
    desired_state: str
    observed_state: str
    namespace: str | None = None
    deployment_name: str | None = None
    workspace_claim_name: str | None = None
    credential_secret_name: str | None = None
    last_error: str | None = None
    updated_at: str = ""


class MagiOut(BaseModel):
    id: int
    name: str | None = None
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    memberships: list[MembershipBrief]
    runtime: RuntimeOut | None = None
    created_at: str = ""
    updated_at: str = ""


class MagiCreate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    magis_id: int = Field(ge=1)
    role_id: int | None = Field(default=None, ge=1)


class MagiUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class InstructionPayload(BaseModel):
    instruction: str = Field(max_length=12000)


class InstructionOut(BaseModel):
    magi_id: int
    instruction: str


def _runtime_magi_id() -> int:
    value = os.environ.get("MAGI_RUNTIME_ID")
    if not value or not value.isdigit():
        raise MagiHTTPException(503, "runtime.identity_missing", "MAGI runtime identity is missing")
    return int(value)


def _served_direct_magis_id(bus: Bus) -> int | None:
    raw = os.environ.get("MAGI_RUNTIME_ID")
    if not raw or not raw.isdigit() or bus.memberships_book is None:
        return None
    membership = bus.memberships_book.get(magi_id=int(raw))
    return membership.magis_id if membership is not None else None


def _require_visible(bus: Bus, magi_id: int) -> None:
    membership = bus.memberships_book.get(magi_id=magi_id) if bus.memberships_book else None
    if membership is None:
        raise MagiHTTPException(404, "not_found.magi", "MAGI not found")
    served = _served_direct_magis_id(bus)
    if served is not None and membership.magis_id != served:
        raise MagiHTTPException(
            403, "forbidden.magi_management_scope", "MAGI is outside the current direct MAGIS"
        )


def _runtime_out(runtime) -> RuntimeOut | None:
    if runtime is None:
        return None
    if runtime.backend_kind == "unprovisioned":
        return RuntimeOut(
            desired_state="draft",
            observed_state="draft",
            deployment_name=runtime.backend_ref,
            updated_at=(
                runtime.updated_at.isoformat()
                if hasattr(runtime.updated_at, "isoformat")
                else str(runtime.updated_at or "")
            ),
        )
    desired = getattr(runtime.desired_state, "value", runtime.desired_state)
    observed = getattr(runtime.observed_state, "value", runtime.observed_state)
    desired = {"started": "running", "stopped": "stopped"}.get(str(desired), str(desired))
    observed = {
        "starting": "provisioning",
        "started": "running",
        "stopping": "stopped",
        "stopped": "stopped",
        "crashed": "failed",
    }.get(str(observed), str(observed))
    return RuntimeOut(
        desired_state=desired,
        observed_state=observed,
        deployment_name=runtime.backend_ref,
        updated_at=(
            runtime.updated_at.isoformat()
            if hasattr(runtime.updated_at, "isoformat")
            else str(runtime.updated_at or "")
        ),
    )


def _magi_out(bus: Bus, membership) -> MagiOut:
    magis = bus.magis_book.get(magis_id=membership.magis_id) if bus.magis_book else None
    role = bus.roles_book.get(role_id=membership.role_id) if bus.roles_book else None
    runtime = (
        bus.runtime_state_book.get(runtime_id=membership.id) if bus.runtime_state_book else None
    )
    return MagiOut(
        id=membership.id,
        name=runtime.backend_ref if runtime else None,
        memberships=[
            MembershipBrief(
                magis_id=membership.magis_id,
                magis_name=magis.name if magis else f"MAGIS {membership.magis_id}",
                role_id=membership.role_id,
                role_name=role.name if role else "",
            )
        ],
        runtime=_runtime_out(runtime),
        created_at=(
            membership.created_at.isoformat()
            if hasattr(membership.created_at, "isoformat")
            else str(membership.created_at or "")
        ),
        updated_at=(
            membership.updated_at.isoformat()
            if hasattr(membership.updated_at, "isoformat")
            else str(membership.updated_at or "")
        ),
    )


def _membership_or_404(bus: Bus, magi_id: int):
    membership = bus.memberships_book.get(magi_id=magi_id) if bus.memberships_book else None
    if membership is None:
        raise MagiHTTPException(404, "not_found.magi", "MAGI not found")
    return membership


def _default_eva_role(bus: Bus, magis_id: int):
    roles = bus.roles_book.list_for_magis(magis_id=magis_id) if bus.roles_book else []
    return next((role for role in roles if role.name == "EVA"), None)


@router.get("/magi", response_model=list[MagiOut])
def list_magi(_admin: AdminGate, bus: BusDep) -> list[MagiOut]:
    memberships = []
    if bus.magis_book and bus.memberships_book:
        served = _served_direct_magis_id(bus)
        for magis in bus.magis_book.list_all():
            if served is None or magis.id == served:
                memberships.extend(bus.memberships_book.list_for_magis(magis_id=magis.id))
    return [_magi_out(bus, item) for item in memberships]


@router.post("/magi", response_model=MagiOut, status_code=201)
def create_magi(payload: MagiCreate, _admin: AdminGate, bus: BusDep) -> MagiOut:
    if bus.magis_book is None or bus.memberships_book is None or bus.roles_book is None:
        raise MagiHTTPException(503, "magis.unavailable", "MAGIS services are unavailable")
    if bus.magis_book.get(magis_id=payload.magis_id) is None:
        raise MagiHTTPException(404, "not_found.magis", "MAGIS not found")
    role = (
        bus.roles_book.get(role_id=payload.role_id)
        if payload.role_id
        else _default_eva_role(bus, payload.magis_id)
    )
    if role is None or role.magis_id != payload.magis_id:
        raise MagiHTTPException(
            400, "validation.magi_role", "role must belong to the selected MAGIS"
        )
    membership = bus.memberships_book.add(magis_id=payload.magis_id, role_id=role.id)
    # The identity is valid immediately.  A runtime is provisioned separately
    # by the node lifecycle; the control row keeps the display label while
    # that provisioning is pending.  Creating it for every identity also
    # makes later rename/delete semantics uniform.
    if bus.runtime_state_book:
        bus.runtime_state_book.upsert(
            runtime_id=membership.id,
            backend_kind="unprovisioned",
            backend_ref=(payload.name or f"EVA-{membership.id:03d}").strip()
            or f"EVA-{membership.id:03d}",
            workspace_dir="",
            log_dir="",
            audit_log_path="",
            port=None,
            base_url=None,
        )
    return _magi_out(bus, membership)


@router.get("/magi/{magi_id}", response_model=MagiOut)
def get_magi(magi_id: int, _admin: AdminGate, bus: BusDep) -> MagiOut:
    _require_visible(bus, magi_id)
    return _magi_out(bus, _membership_or_404(bus, magi_id))


@router.patch("/magi/{magi_id}", response_model=MagiOut)
def update_magi(magi_id: int, payload: MagiUpdate, _admin: AdminGate, bus: BusDep) -> MagiOut:
    _require_visible(bus, magi_id)
    if bus.runtime_state_book is None:
        raise MagiHTTPException(503, "runtime.unavailable", "runtime registry is unavailable")
    runtime = bus.runtime_state_book.rename(runtime_id=magi_id, backend_ref=payload.name)
    if runtime is None:
        raise MagiHTTPException(
            409, "runtime.not_provisioned", "MAGI has no control runtime record"
        )
    return _magi_out(bus, _membership_or_404(bus, magi_id))


def _set_lifecycle(bus: Bus, *, magi_id: int, desired_state: RuntimeDesiredState) -> RuntimeOut:
    _require_visible(bus, magi_id)
    if magi_id == _runtime_magi_id_optional():
        raise MagiHTTPException(
            409, "runtime.current_magi_protected", "Cannot stop the MAGI serving this request"
        )
    if bus.runtime_state_book is None:
        raise MagiHTTPException(503, "runtime.unavailable", "runtime registry is unavailable")
    existing = bus.runtime_state_book.get(runtime_id=magi_id)
    if existing is None or existing.backend_kind == "unprovisioned":
        raise MagiHTTPException(
            409, "runtime.not_provisioned", "Provision this MAGI before changing its lifecycle"
        )
    runtime = bus.runtime_state_book.set_desired_state(
        runtime_id=magi_id, desired_state=desired_state
    )
    result = _runtime_out(runtime)
    assert result is not None
    return result


def _runtime_magi_id_optional() -> int | None:
    raw = os.environ.get("MAGI_RUNTIME_ID")
    return int(raw) if raw and raw.isdigit() else None


@router.post("/magi/{magi_id}/runtime/start", response_model=RuntimeOut)
def start_runtime(magi_id: int, _admin: AdminGate, bus: BusDep) -> RuntimeOut:
    return _set_lifecycle(bus, magi_id=magi_id, desired_state=RuntimeDesiredState.STARTED)


@router.post("/magi/{magi_id}/runtime/stop", response_model=RuntimeOut)
def stop_runtime(magi_id: int, _admin: AdminGate, bus: BusDep) -> RuntimeOut:
    return _set_lifecycle(bus, magi_id=magi_id, desired_state=RuntimeDesiredState.STOPPED)


@router.delete("/magi/{magi_id}", status_code=204)
def delete_magi(magi_id: int, _admin: AdminGate, bus: BusDep) -> Response:
    _require_visible(bus, magi_id)
    if magi_id == _runtime_magi_id_optional():
        raise MagiHTTPException(
            409, "runtime.current_magi_protected", "Cannot delete the MAGI serving this request"
        )
    runtime = bus.runtime_state_book.get(runtime_id=magi_id) if bus.runtime_state_book else None
    if runtime is not None and runtime.backend_kind != "unprovisioned":
        raise MagiHTTPException(
            409,
            "runtime.deprovision_required",
            "Deprovision the runtime before removing its identity",
        )
    if runtime is not None and bus.runtime_state_book:
        bus.runtime_state_book.remove(runtime_id=magi_id)
    if not bus.memberships_book or not bus.memberships_book.remove(magi_id=magi_id):
        raise MagiHTTPException(404, "not_found.magi", "MAGI not found")
    return Response(status_code=204)


@self_router.get("/magi/self/instruction", response_model=InstructionOut)
def get_self_instruction(_admin: AdminGate, bus: BusDep) -> InstructionOut:
    magi_id = _runtime_magi_id()
    return InstructionOut(
        magi_id=magi_id,
        instruction=bus.settings_book.get(key="instruction") or "",
    )


@self_router.put("/magi/self/instruction", response_model=InstructionOut)
def put_self_instruction(
    payload: InstructionPayload, _admin: AdminGate, bus: BusDep
) -> InstructionOut:
    magi_id = _runtime_magi_id()
    bus.settings_book.set(key="instruction", value=payload.instruction)
    return InstructionOut(magi_id=magi_id, instruction=payload.instruction)


__all__ = ["router", "self_router"]
