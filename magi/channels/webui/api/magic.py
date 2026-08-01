"""MAGI CRUD and lifecycle API.

A MAGI is created independently.  MAGIS membership and role assignment live
in the MAGIS API, so creation does not imply either membership or a running
container.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.db import EveRuntime, MAGIC, MAGIS, MAGISMembership, MAGISRole
from magi.db.magis import get_magis_session
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magic")
router = APIRouter(tags=["magic"])


def _admin_gate(request: Request) -> str:
    from magi.channels.webui.api.auth_gates import admin_gate
    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


class MembershipBrief(BaseModel):
    magis_id: int
    magis_name: str
    role_id: int
    role_name: str


class EveRuntimeOut(BaseModel):
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
    runtime: EveRuntimeOut | None = None
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


def _runtime_out(runtime: EveRuntime | None) -> EveRuntimeOut | None:
    if runtime is None:
        return None
    return EveRuntimeOut(
        desired_state=runtime.desired_state, observed_state=runtime.observed_state,
        namespace=runtime.namespace, deployment_name=runtime.deployment_name,
        workspace_claim_name=runtime.workspace_claim_name,
        credential_secret_name=runtime.credential_secret_name, last_error=runtime.last_error,
        updated_at=runtime.updated_at.isoformat() if runtime.updated_at else "",
    )


def _serialize(session: Session, magic: MAGIC, runtime: EveRuntime | None = None) -> MAGICOut:
    memberships = session.execute(
        select(MAGISMembership, MAGISRole, MAGIS)
        .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
        .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
        .where(MAGISMembership.magic_id == magic.id)
        .order_by(MAGISMembership.id)
    ).all()
    return MAGICOut(
        id=magic.id, name=magic.name, provider=magic.provider,
        api_key_set=bool(magic.api_key), api_key_last4=(magic.api_key[-4:] if magic.api_key else None),
        memberships=[MembershipBrief(magis_id=m.magis_id, magis_name=s.name, role_id=r.id, role_name=r.name) for m, r, s in memberships],
        runtime=_runtime_out(runtime),
        created_at=magic.created_at.isoformat() if magic.created_at else "",
        updated_at=magic.updated_at.isoformat() if magic.updated_at else "",
    )


def _served_direct_magis_id(session: Session) -> int | None:
    current = _current_magic(session)
    binding = session.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == current.id))
    return binding.magis_id if binding is not None else None


def _require_visible_magic(session: Session, magic: MAGIC, *, allow_unassigned: bool = True) -> None:
    binding = _direct_magis_binding(session, magic)
    served = _served_direct_magis_id(session)
    if binding is None and allow_unassigned:
        return
    if binding is None or served is None or binding[0].magis_id != served:
        raise MagiHTTPException(403, "forbidden.magic_management_scope", "MAGI is outside the current direct MAGIS")


@router.get("/magic", response_model=list[MAGICOut])
def list_magic(_admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> list[MAGICOut]:
    served = _served_direct_magis_id(session)
    direct_ids = select(MAGISMembership.magic_id).where(MAGISMembership.magis_id == served) if served else select(MAGISMembership.magic_id).where(False)
    assigned_ids = select(MAGISMembership.magic_id)
    rows = session.scalars(select(MAGIC).where((MAGIC.id.in_(direct_ids)) | (~MAGIC.id.in_(assigned_ids))).order_by(MAGIC.id)).all()
    runtimes = {r.magic_id: r for r in session.scalars(select(EveRuntime).where(EveRuntime.magic_id.in_([m.id for m in rows]))).all()} if rows else {}
    return [_serialize(session, magic, runtimes.get(magic.id)) for magic in rows]


@router.post("/magic", response_model=MAGICOut, status_code=201)
def create_magic(payload: MAGICCreate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGICOut:
    magic = MAGIC(name=payload.name, provider=payload.provider, api_key=payload.api_key)
    session.add(magic)
    session.commit()
    session.refresh(magic)
    return _serialize(session, magic)


def _magic_or_404(session: Session, magic_id: int) -> MAGIC:
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(404, "not_found.magic", "MAGI not found")
    return magic


@router.get("/magic/{magic_id}", response_model=MAGICOut)
def get_magic(magic_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGICOut:
    magic = _magic_or_404(session, magic_id)
    _require_visible_magic(session, magic)
    return _serialize(session, magic, session.scalar(select(EveRuntime).where(EveRuntime.magic_id == magic.id)))


@router.patch("/magic/{magic_id}", response_model=MAGICOut)
def update_magic(magic_id: int, payload: MAGICUpdate, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> MAGICOut:
    magic = _magic_or_404(session, magic_id)
    _require_visible_magic(session, magic)
    for field in ("name", "provider"):
        if field in payload.model_fields_set:
            setattr(magic, field, getattr(payload, field))
    if "api_key" in payload.model_fields_set:
        magic.api_key = payload.api_key or None
    session.commit()
    return _serialize(session, magic, session.scalar(select(EveRuntime).where(EveRuntime.magic_id == magic.id)))


def _current_magic(session: Session) -> MAGIC:
    """Resolve the MAGI served by this WebUI container."""
    import os
    runtime_id = os.environ.get("MAGI_RUNTIME_ID")
    if runtime_id and runtime_id.isdigit():
        return _magic_or_404(session, int(runtime_id))
    root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
    if root and root.adam_id:
        return _magic_or_404(session, root.adam_id)
    raise MagiHTTPException(404, "not_found.current_magic", "this runtime is not bound to a MAGI")


@router.get("/magic/self/instruction", response_model=InstructionOut)
def get_self_instruction(_admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> InstructionOut:
    magic = _current_magic(session)
    return InstructionOut(magic_id=magic.id, instruction=magic.instruction)


@router.put("/magic/self/instruction", response_model=InstructionOut)
def put_self_instruction(payload: InstructionPayload, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> InstructionOut:
    magic = _current_magic(session)
    magic.instruction = payload.instruction
    session.commit()
    return InstructionOut(magic_id=magic.id, instruction=magic.instruction)


def _runtime_or_create(session: Session, magic: MAGIC) -> EveRuntime:
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magic_id == magic.id))
    if runtime is None:
        runtime = EveRuntime(magic_id=magic.id)
        session.add(runtime)
        session.flush()
    return runtime


def _direct_magis_binding(session: Session, magic: MAGIC):
    """Return the MAGI's one direct MAGIS, role, and membership."""
    row = session.execute(
        select(MAGISMembership, MAGISRole, MAGIS)
        .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
        .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
        .where(MAGISMembership.magic_id == magic.id).order_by(MAGISMembership.id)
    ).first()
    return row


def _apply_result(runtime: EveRuntime, result) -> None:
    runtime.observed_state = result.observed_state
    runtime.namespace, runtime.deployment_name = result.namespace, result.deployment_name
    runtime.workspace_claim_name = result.workspace_claim_name
    runtime.credential_secret_name, runtime.last_error = result.credential_secret_name, None


@router.get("/magic/{magic_id}/runtime", response_model=EveRuntimeOut)
def get_runtime(magic_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> EveRuntimeOut:
    magic = _magic_or_404(session, magic_id)
    _require_visible_magic(session, magic, allow_unassigned=False)
    return _runtime_out(_runtime_or_create(session, magic))  # type: ignore[return-value]


def _lifecycle(action: str, magic_id: int, session: Session) -> EveRuntimeOut:
    magic = _magic_or_404(session, magic_id)
    _require_visible_magic(session, magic, allow_unassigned=False)
    if magic.id == _current_magic(session).id:
        raise MagiHTTPException(409, "runtime.current_magic_protected", "Cannot stop or restart the MAGI currently serving this session")
    runtime = _runtime_or_create(session, magic)
    direct_binding = _direct_magis_binding(session, magic)
    if action == "start":
        if direct_binding is None:
            raise MagiHTTPException(400, "validation.magic_membership_required", "assign this MAGI to a MAGIS before starting it")
        if not magic.provider or not magic.api_key:
            raise MagiHTTPException(400, "validation.eve_provider_credentials_required", "configure provider and API key before starting this MAGI")
    from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
    from magi.orchestrator.contracts import EveSpec
    runtime.desired_state = "running" if action == "start" else "stopped"
    from magi.orchestrator.contracts import MagisBinding, MagisRuntimeConfiguration
    magis = MagisBinding(id=direct_binding[2].id, name=direct_binding[2].name) if direct_binding else None
    configuration = (
        MagisRuntimeConfiguration(
            magis_instruction=direct_binding[2].instruction,
            role_name=direct_binding[1].name,
            role_instruction=direct_binding[1].instruction,
            magic_name=magic.name,
            personal_instruction=magic.instruction,
            provider=magic.provider,
            api_key=magic.api_key,
        )
        if direct_binding else None
    )
    spec = EveSpec(magic_id=magic.id, name=magic.name, magis=magis, configuration=configuration)
    try:
        result = request_lifecycle(action, spec)
    except OrchestratorUnavailable as exc:
        runtime.observed_state, runtime.last_error = "failed", str(exc)
        session.commit()
        raise MagiHTTPException(503, "runtime.orchestrator_unavailable", str(exc)) from exc
    _apply_result(runtime, result)
    session.commit()
    return _runtime_out(runtime)  # type: ignore[return-value]


@router.post("/magic/{magic_id}/runtime/start", response_model=EveRuntimeOut)
def start_runtime(magic_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> EveRuntimeOut:
    return _lifecycle("start", magic_id, session)


@router.post("/magic/{magic_id}/runtime/stop", response_model=EveRuntimeOut)
def stop_runtime(magic_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> EveRuntimeOut:
    return _lifecycle("stop", magic_id, session)


@router.delete("/magic/{magic_id}", status_code=204)
def delete_magic(magic_id: int, _admin: AdminGate, session: Annotated[Session, Depends(get_magis_session)]) -> Response:
    magic = _magic_or_404(session, magic_id)
    _require_visible_magic(session, magic)
    if magic.id == _current_magic(session).id:
        raise MagiHTTPException(409, "runtime.current_magic_protected", "Cannot delete the MAGI currently serving this session")
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magic_id == magic.id))
    if runtime and runtime.deployment_name:
        from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
        from magi.orchestrator.contracts import EveSpec
        try:
            request_lifecycle("delete", EveSpec(magic_id=magic.id, name=magic.name))
        except OrchestratorUnavailable as exc:
            raise MagiHTTPException(503, "runtime.orchestrator_unavailable", str(exc)) from exc
    session.delete(magic)
    session.commit()
    return Response(status_code=204)
