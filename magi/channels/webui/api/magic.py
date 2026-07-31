"""MAGIC CRUD API — the MAGI Citizens surface.

Each row is a :class:`MAGIC` (a MAGI runtime agent bound to
a :class:`MAGIS` via ``magis_id``). ``magic_position`` is
one of ``"adam"`` (the manager — exactly one per ``MAGIS``)
or ``"eve"`` (a worker — N per ``MAGIS``).

The MAGIS Society tree endpoints live in :mod:`magi.channels.webui.api.magis`
(GET/POST/PATCH/DELETE ``/api/magis``). This module only
manages the per-MAGIS ``MAGIC`` rows.

All routes require the caller to be signed in **and** an
admin (a ``Contact`` row with ``role='admin'``); both
checks run via the shared :func:`admin_gate` dependency in
:mod:`magi.channels.webui.api.auth_gates`.

Notes
-----

- ``provider`` / ``api_key`` are write-only on PATCH (the
  key is never read back; only a ``last4`` is returned in
  GET responses, mirroring :class:`Contact`).
- The seeded root MAGIS is guaranteed to have one
  ``MAGIC(magic_position='adam')`` row at boot — see
  :func:`magi.agent.db.engine._seed_default_root`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.agent.db import MAGIS, EveRuntime, MAGIC, get_session
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magic")

router = APIRouter(tags=["magic"])


# -- admin gate -------------------------------------------------------------
#
# Reuse the auth_gates module so the admin check lives in
# one place.


def _admin_gate(request: Request) -> str:
    from magi.channels.webui.api.auth_gates import admin_gate

    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


# -- response / payload shapes ----------------------------------------------

_MAGI_POSITIONS: tuple[str, ...] = ("adam", "eve")


def _mask_key(raw: str | None) -> tuple[bool, str | None]:
    if not raw:
        return False, None
    return True, (raw[-4:] if len(raw) >= 4 else raw)


class MAGISBrief(BaseModel):
    """The bits of a MAGIS row the magic list needs to render
    a "team" column without an extra round-trip per row."""

    id: int
    name: str | None = None
    magic_position: str | None = None


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
    magis_id: int
    magic_position: str
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    runtime: EveRuntimeOut | None = None
    created_at: str
    updated_at: str


class MAGICCreate(BaseModel):
    magis_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=100)
    magic_position: str = Field(min_length=1, max_length=16)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


class MAGICUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    magic_position: str | None = Field(default=None, max_length=16)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


def _serialize_runtime(runtime: EveRuntime | None) -> EveRuntimeOut | None:
    if runtime is None:
        return None
    return EveRuntimeOut(
        desired_state=runtime.desired_state,
        observed_state=runtime.observed_state,
        namespace=runtime.namespace,
        deployment_name=runtime.deployment_name,
        workspace_claim_name=runtime.workspace_claim_name,
        credential_secret_name=runtime.credential_secret_name,
        last_error=runtime.last_error,
        updated_at=runtime.updated_at.isoformat() if runtime.updated_at else "",
    )


def _serialize(m: MAGIC, runtime: EveRuntime | None = None) -> MAGICOut:
    is_set, last4 = _mask_key(m.api_key)
    return MAGICOut(
        id=m.id,
        name=m.name,
        magis_id=m.magis_id,
        magic_position=m.magic_position,
        provider=m.provider,
        api_key_set=is_set,
        api_key_last4=last4,
        runtime=_serialize_runtime(runtime),
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# -- endpoints --------------------------------------------------------------


@router.get("/magic", response_model=list[MAGICOut])
def list_magic(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    magis_id: int | None = None,
) -> list[MAGICOut]:
    """Return every ``MAGIC`` row, optionally filtered by ``magis_id``."""
    q = select(MAGIC).order_by(MAGIC.id.asc())
    if magis_id is not None:
        q = q.where(MAGIC.magis_id == magis_id)
    rows = session.scalars(q).all()
    runtime_by_magic = (
        {
            runtime.magi_id: runtime
            for runtime in session.scalars(
                select(EveRuntime).where(EveRuntime.magi_id.in_([r.id for r in rows]))
            ).all()
        }
        if rows
        else {}
    )
    return [_serialize(r, runtime_by_magic.get(r.id)) for r in rows]


@router.post("/magic", response_model=MAGICOut, status_code=201)
def create_magic(
    payload: MAGICCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGICOut:
    """Create a new ``MAGIC`` row."""
    if payload.magic_position not in _MAGI_POSITIONS:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magic_position_unknown",
            detail=(
                f"Unknown magic_position {payload.magic_position!r}. "
                f"Valid: {', '.join(_MAGI_POSITIONS)}"
            ),
        )
    magis = session.get(MAGIS, payload.magis_id)
    if magis is None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magis_id_not_found",
            detail=f"magis_id {payload.magis_id} not found",
        )

    if payload.magic_position == "adam" and magis.adam_id is not None:
        raise MagiHTTPException(
            status_code=409,
            code="validation.adam_already_assigned",
            detail=(
                f"MAGIS {payload.magis_id} already has an adam "
                f"(magic id={magis.adam_id}); unbind the existing "
                f"adam or use PATCH /api/magis to reassign"
            ),
        )

    magic = MAGIC(
        name=payload.name,
        magis_id=payload.magis_id,
        magic_position=payload.magic_position,
        provider=payload.provider,
        api_key=payload.api_key,
    )
    session.add(magic)
    session.flush()
    runtime: EveRuntime | None = None
    if payload.magic_position == "eve":
        runtime = EveRuntime(magi_id=magic.id)
        session.add(runtime)
    if payload.magic_position == "adam":
        from sqlalchemy import update as _sa_update

        session.execute(_sa_update(MAGIS).where(MAGIS.id == magis.id).values(adam_id=magic.id))
    session.commit()
    session.refresh(magic)
    if runtime is not None:
        session.refresh(runtime)
    return _serialize(magic, runtime)


@router.get("/magic/{magic_id}", response_model=MAGICOut)
def get_magic(
    magic_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGICOut:
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magic",
            detail="MAGIC not found",
        )
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magic.id))
    return _serialize(magic, runtime)


@router.patch("/magic/{magic_id}", response_model=MAGICOut)
def update_magic(
    magic_id: int,
    payload: MAGICUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGICOut:
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magic",
            detail="MAGIC not found",
        )

    if "name" in payload.model_fields_set and payload.name is not None:
        magic.name = payload.name

    if "magic_position" in payload.model_fields_set and payload.magic_position is not None:
        if payload.magic_position not in _MAGI_POSITIONS:
            raise MagiHTTPException(
                status_code=400,
                code="validation.magic_position_unknown",
                detail=(
                    f"Unknown magic_position {payload.magic_position!r}. "
                    f"Valid: {', '.join(_MAGI_POSITIONS)}"
                ),
            )
        magic.magic_position = payload.magic_position

    if "provider" in payload.model_fields_set:
        magic.provider = payload.provider

    if "api_key" in payload.model_fields_set:
        magic.api_key = payload.api_key if payload.api_key else None

    session.commit()
    session.refresh(magic)
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magic.id))
    return _serialize(magic, runtime)


def _eve_runtime_or_404(session: Session, magic_id: int) -> tuple[MAGIC, EveRuntime]:
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(status_code=404, code="not_found.magic", detail="MAGIC not found")
    if magic.magic_position != "eve":
        raise MagiHTTPException(
            status_code=400,
            code="validation.eve_runtime_requires_eve",
            detail="only an EVE MAGIC can have a managed runtime",
        )
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magic.id))
    if runtime is None:
        runtime = EveRuntime(magi_id=magic.id)
        session.add(runtime)
        session.flush()
    return magic, runtime


def _apply_orchestrator_result(runtime: EveRuntime, result) -> None:
    runtime.observed_state = result.observed_state
    runtime.namespace = result.namespace
    runtime.deployment_name = result.deployment_name
    runtime.workspace_claim_name = result.workspace_claim_name
    runtime.credential_secret_name = result.credential_secret_name
    runtime.last_error = None


@router.get("/magic/{magic_id}/runtime", response_model=EveRuntimeOut)
def get_eve_runtime(
    magic_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EveRuntimeOut:
    _magic, runtime = _eve_runtime_or_404(session, magic_id)
    session.commit()
    session.refresh(runtime)
    return _serialize_runtime(runtime)  # type: ignore[return-value]


@router.post("/magic/{magic_id}/runtime/start", response_model=EveRuntimeOut)
def start_eve_runtime(
    magic_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EveRuntimeOut:
    magic, runtime = _eve_runtime_or_404(session, magic_id)
    if not magic.provider or not magic.api_key:
        raise MagiHTTPException(
            status_code=400,
            code="validation.eve_provider_credentials_required",
            detail="configure an EVE provider and API key before starting it",
        )
    from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
    from magi.orchestrator.contracts import EveSpec

    runtime.desired_state = "running"
    spec = EveSpec(
        magi_id=magic.id,
        magis_id=magic.magis_id,
        name=magic.name,
        provider=magic.provider,
        api_key=magic.api_key,
    )
    try:
        result = request_lifecycle("start", spec)
    except OrchestratorUnavailable as exc:
        runtime.observed_state = "failed"
        runtime.last_error = str(exc)
        session.commit()
        raise MagiHTTPException(
            status_code=503, code="runtime.orchestrator_unavailable", detail=str(exc)
        ) from exc
    _apply_orchestrator_result(runtime, result)
    session.commit()
    session.refresh(runtime)
    return _serialize_runtime(runtime)  # type: ignore[return-value]


@router.post("/magic/{magic_id}/runtime/stop", response_model=EveRuntimeOut)
def stop_eve_runtime(
    magic_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EveRuntimeOut:
    magic, runtime = _eve_runtime_or_404(session, magic_id)
    from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
    from magi.orchestrator.contracts import EveSpec

    runtime.desired_state = "stopped"
    try:
        result = request_lifecycle(
            "stop", EveSpec(magi_id=magic.id, magis_id=magic.magis_id, name=magic.name)
        )
    except OrchestratorUnavailable as exc:
        runtime.observed_state = "failed"
        runtime.last_error = str(exc)
        session.commit()
        raise MagiHTTPException(
            status_code=503, code="runtime.orchestrator_unavailable", detail=str(exc)
        ) from exc
    _apply_orchestrator_result(runtime, result)
    session.commit()
    session.refresh(runtime)
    return _serialize_runtime(runtime)  # type: ignore[return-value]


@router.delete("/magic/{magic_id}", status_code=204)
def delete_magic(
    magic_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magic",
            detail="MAGIC not found",
        )
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magic.id))
    if magic.magic_position == "eve" and runtime is not None and runtime.deployment_name:
        from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
        from magi.orchestrator.contracts import EveSpec

        try:
            request_lifecycle(
                "delete", EveSpec(magi_id=magic.id, magis_id=magic.magis_id, name=magic.name)
            )
        except OrchestratorUnavailable as exc:
            raise MagiHTTPException(
                status_code=503, code="runtime.orchestrator_unavailable", detail=str(exc)
            ) from exc
    if magic.magic_position == "adam":
        magis = session.get(MAGIS, magic.magis_id)
        if magis is not None and magis.adam_id == magic_id:
            from sqlalchemy import update as _sa_update

            session.execute(
                _sa_update(MAGIS)
                .where(MAGIS.id == magis.id, MAGIS.adam_id == magic_id)
                .values(adam_id=None)
            )
    session.delete(magic)
    session.commit()
    return Response(status_code=204)
