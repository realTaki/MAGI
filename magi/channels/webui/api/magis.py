"""Magi CRUD API — the "智能体管理 / magis 管理" surface.

Each row is a :class:`Magi` (a MAGI runtime agent bound to
a :class:`MAGIC` via ``magic_id``). ``magic_position`` is
one of ``"adam"`` (the manager — exactly one per ``MAGIC``)
or ``"eve"`` (a worker — N per ``MAGIC``).

The MAGIC tree endpoints live in :mod:`magi.channels.webui.api.magics`
(GET/POST/PATCH/DELETE ``/api/magics``). This module only
manages the per-MAGIC ``Magi`` rows.

All routes require the caller to be signed in **and** an
admin (a ``Contact`` row with ``role='admin'``); both
checks run via the shared :func:`admin_gate` dependency in
:mod:`magi.channels.webui.api.auth_gates`.

Notes
-----

- ``provider`` / ``api_key`` are write-only on PATCH (the
  key is never read back; only a ``last4`` is returned in
  GET responses, mirroring :class:`Contact`).
- The seeded root MAGIC is guaranteed to have one
  ``Magi(magic_position='adam')`` row at boot — see
  :func:`magi.agent.db.engine._seed_default_root`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.agent.db import MAGIC, EveRuntime, Magi, get_session

# MAGIC is imported for the POST /magis validation
# (checking that magic_id references an existing MAGIC).
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magis")

router = APIRouter(tags=["magis"])


# -- admin gate -------------------------------------------------------------
#
# Reuse the auth_gates module so the admin check lives in
# one place.


def _admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is an admin.

    Delegates to :func:`magi.channels.webui.api.auth_gates.admin_gate`
    which reads the ``magi_session`` cookie and confirms
    ``Contact.role == 'admin'``.
    """
    from magi.channels.webui.api.auth_gates import admin_gate

    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


# -- response / payload shapes ----------------------------------------------

_MAGI_POSITIONS: tuple[str, ...] = ("adam", "eve")


def _mask_key(raw: str | None) -> tuple[bool, str | None]:
    """Return ``(is_set, last4_or_None)`` from a stored key.

    Mirrors :func:`magi.channels.webui.api.contacts._mask_key`
    so the policy lives in one place; the implementation is
    duplicated here to avoid a cross-router import cycle.
    """
    if not raw:
        return False, None
    return True, (raw[-4:] if len(raw) >= 4 else raw)


class MagiBrief(BaseModel):
    """The bits of a MAGI team row the magis list needs to render
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


class MagiOut(BaseModel):
    id: int
    name: str | None = None
    magic_id: int
    magic_position: str
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    runtime: EveRuntimeOut | None = None
    created_at: str
    updated_at: str


class MagiCreate(BaseModel):
    magic_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=100)
    magic_position: str = Field(min_length=1, max_length=16)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


class MagiUpdate(BaseModel):
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


def _serialize(m: Magi, runtime: EveRuntime | None = None) -> MagiOut:
    is_set, last4 = _mask_key(m.api_key)
    return MagiOut(
        id=m.id,
        name=m.name,
        magic_id=m.magic_id,
        magic_position=m.magic_position,
        provider=m.provider,
        api_key_set=is_set,
        api_key_last4=last4,
        runtime=_serialize_runtime(runtime),
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# -- endpoints --------------------------------------------------------------


@router.get("/magis", response_model=list[MagiOut])
def list_magis(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    magic_id: int | None = None,
) -> list[MagiOut]:
    """Return every ``Magi`` row, optionally filtered by ``magic_id``.

    Sorted by ``id`` so the seeded adam (id=1) lands first and
    the operator gets a stable list view.
    """
    q = select(Magi).order_by(Magi.id.asc())
    if magic_id is not None:
        q = q.where(Magi.magic_id == magic_id)
    rows = session.scalars(q).all()
    runtime_by_magi = (
        {
            runtime.magi_id: runtime
            for runtime in session.scalars(
                select(EveRuntime).where(EveRuntime.magi_id.in_([m.id for m in rows]))
            ).all()
        }
        if rows
        else {}
    )
    return [_serialize(m, runtime_by_magi.get(m.id)) for m in rows]


@router.post("/magis", response_model=MagiOut, status_code=201)
def create_magi(
    payload: MagiCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MagiOut:
    """Create a new ``Magi`` row.

    ``magic_position`` is validated against the enum so a typo
    doesn't sneak a bad value into the DB. ``magic_id`` must
    reference an existing ``MAGIC`` row (404 otherwise).
    """
    if payload.magic_position not in _MAGI_POSITIONS:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magic_position_unknown",
            detail=(
                f"Unknown magic_position {payload.magic_position!r}. "
                f"Valid: {', '.join(_MAGI_POSITIONS)}"
            ),
        )
    magic = session.get(MAGIC, payload.magic_id)
    if magic is None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magic_id_not_found",
            detail=f"magic_id {payload.magic_id} not found",
        )

    # Adam invariant: a MAGIC can have at most one Magi with
    # ``magic_position='adam'``. Refuse with 409 rather than
    # silently overwriting an existing binding — the operator
    # should PATCH the existing adam's MAGIC via
    # ``/api/magics/{id}`` (which validates the magic_position
    # target), or unbind the old adam first.
    if payload.magic_position == "adam" and magic.adam_id is not None:
        raise MagiHTTPException(
            status_code=409,
            code="validation.adam_already_assigned",
            detail=(
                f"MAGIC {payload.magic_id} already has an adam "
                f"(magi id={magic.adam_id}); unbind the existing "
                f"adam or use PATCH /api/magics to reassign"
            ),
        )

    magi = Magi(
        name=payload.name,
        magic_id=payload.magic_id,
        magic_position=payload.magic_position,
        provider=payload.provider,
        api_key=payload.api_key,
    )
    session.add(magi)
    session.flush()  # populate ``magi.id``
    runtime: EveRuntime | None = None
    if payload.magic_position == "eve":
        runtime = EveRuntime(magi_id=magi.id)
        session.add(runtime)
    if payload.magic_position == "adam":
        # Bind the new adam to the MAGIC row via raw Core
        # ``update()``. Mutating ``magic.adam_id`` on the
        # ORM-mapped ``MAGIC`` instance triggers SQLAlchemy's
        # self-referential cascade, which NULLs the column
        # rather than writing the new FK value. Raw Core
        # bypasses the ORM entirely.
        from sqlalchemy import update as _sa_update

        session.execute(_sa_update(MAGIC).where(MAGIC.id == magic.id).values(adam_id=magi.id))
    session.commit()
    session.refresh(magi)
    if runtime is not None:
        session.refresh(runtime)
    return _serialize(magi, runtime)


@router.get("/magis/{magi_id}", response_model=MagiOut)
def get_magi(
    magi_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MagiOut:
    magi = session.get(Magi, magi_id)
    if magi is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magi",
            detail="magi not found",
        )
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magi.id))
    return _serialize(magi, runtime)


@router.patch("/magis/{magi_id}", response_model=MagiOut)
def update_magi(
    magi_id: int,
    payload: MagiUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MagiOut:
    magi = session.get(Magi, magi_id)
    if magi is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magi",
            detail="magi not found",
        )

    if "name" in payload.model_fields_set and payload.name is not None:
        magi.name = payload.name

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
        magi.magic_position = payload.magic_position

    if "provider" in payload.model_fields_set:
        magi.provider = payload.provider

    if "api_key" in payload.model_fields_set:
        # Write-only: None = don't change, "" = clear, "<x>" = set
        magi.api_key = payload.api_key if payload.api_key else None

    session.commit()
    session.refresh(magi)
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magi.id))
    return _serialize(magi, runtime)


def _eve_runtime_or_404(session: Session, magi_id: int) -> tuple[Magi, EveRuntime]:
    magi = session.get(Magi, magi_id)
    if magi is None:
        raise MagiHTTPException(status_code=404, code="not_found.magi", detail="magi not found")
    if magi.magic_position != "eve":
        raise MagiHTTPException(
            status_code=400,
            code="validation.eve_runtime_requires_eve",
            detail="only an EVE Magi can have a managed runtime",
        )
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magi.id))
    if runtime is None:
        # Compatibility for EVE rows created before the lifecycle migration.
        runtime = EveRuntime(magi_id=magi.id)
        session.add(runtime)
        session.flush()
    return magi, runtime


def _apply_orchestrator_result(runtime: EveRuntime, result) -> None:
    runtime.observed_state = result.observed_state
    runtime.namespace = result.namespace
    runtime.deployment_name = result.deployment_name
    runtime.workspace_claim_name = result.workspace_claim_name
    runtime.credential_secret_name = result.credential_secret_name
    runtime.last_error = None


@router.get("/magis/{magi_id}/runtime", response_model=EveRuntimeOut)
def get_eve_runtime(
    magi_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EveRuntimeOut:
    _magi, runtime = _eve_runtime_or_404(session, magi_id)
    session.commit()
    session.refresh(runtime)
    return _serialize_runtime(runtime)  # type: ignore[return-value]


@router.post("/magis/{magi_id}/runtime/start", response_model=EveRuntimeOut)
def start_eve_runtime(
    magi_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EveRuntimeOut:
    magi, runtime = _eve_runtime_or_404(session, magi_id)
    if not magi.provider or not magi.api_key:
        raise MagiHTTPException(
            status_code=400,
            code="validation.eve_provider_credentials_required",
            detail="configure an EVE provider and API key before starting it",
        )
    from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
    from magi.orchestrator.contracts import EveSpec

    runtime.desired_state = "running"
    spec = EveSpec(
        magi_id=magi.id,
        magic_id=magi.magic_id,
        name=magi.name,
        provider=magi.provider,
        api_key=magi.api_key,
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


@router.post("/magis/{magi_id}/runtime/stop", response_model=EveRuntimeOut)
def stop_eve_runtime(
    magi_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> EveRuntimeOut:
    magi, runtime = _eve_runtime_or_404(session, magi_id)
    from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
    from magi.orchestrator.contracts import EveSpec

    runtime.desired_state = "stopped"
    try:
        result = request_lifecycle(
            "stop", EveSpec(magi_id=magi.id, magic_id=magi.magic_id, name=magi.name)
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


@router.delete("/magis/{magi_id}", status_code=204)
def delete_magi(
    magi_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    magi = session.get(Magi, magi_id)
    if magi is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magi",
            detail="magi not found",
        )
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magi_id == magi.id))
    if magi.magic_position == "eve" and runtime is not None and runtime.deployment_name:
        # A UI delete is an explicit destructive action.  Remove the external
        # resource set before deleting the DB row so a controller outage can
        # never leave an orphaned PVC/Secret behind silently.
        from magi.orchestrator.client import OrchestratorUnavailable, request_lifecycle
        from magi.orchestrator.contracts import EveSpec

        try:
            request_lifecycle(
                "delete", EveSpec(magi_id=magi.id, magic_id=magi.magic_id, name=magi.name)
            )
        except OrchestratorUnavailable as exc:
            raise MagiHTTPException(
                status_code=503, code="runtime.orchestrator_unavailable", detail=str(exc)
            ) from exc
    # Clear the MAGIC's adam_id if this was the bound Adam
    # (via raw Core UPDATE — ORM-side ``magic.adam_id = None``
    # is silently dropped by self-referential cascade handling).
    if magi.magic_position == "adam":
        magic = session.get(MAGIC, magi.magic_id)
        if magic is not None and magic.adam_id == magi_id:
            from sqlalchemy import update as _sa_update

            session.execute(
                _sa_update(MAGIC)
                .where(MAGIC.id == magic.id, MAGIC.adam_id == magi_id)
                .values(adam_id=None)
            )
    session.delete(magi)
    session.commit()
    return Response(status_code=204)
