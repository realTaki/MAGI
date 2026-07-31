"""MAGIS CRUD API — the "MAGI 团体 / MAGIS (MAGI Societies)" surface.

Each row is a :class:`MAGIS` (a MAGI Society — a group of
MAGIs). MAGIS groups form a tree-structured org: a parent MAGIS
holds child MAGIS groups (sub-societies). Each MAGIS has exactly
one ``adam`` MAGIC (the manager agent).

All routes require the caller to be signed in **and** an
admin (a ``Contact`` row with ``role='admin'``); the
``admin_gate`` dependency is shared from
:mod:`magi.channels.webui.api.auth_gates`.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from magi.agent.db import (
    MAGIS,
    MAGIC,
    get_session,
)
from magi.agent.db.base import utcnow_naive
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magis")

router = APIRouter(tags=["magis"])


# -- admin gate -------------------------------------------------------------


def _admin_gate(request: Request) -> str:
    from magi.channels.webui.api.auth_gates import admin_gate

    return admin_gate(request)


from typing import Annotated

AdminGate = Annotated[str, Depends(_admin_gate)]


# -- response / payload shapes ----------------------------------------------


class MAGISOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    adam_id: int | None
    child_count: int = 0
    member_count: int = 0
    created_at: str
    updated_at: str


class MAGISCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int = Field(ge=1)


class MAGISUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = Field(default=None, ge=1)
    adam_id: int | None = Field(default=None, ge=1)


def _serialize_magis(m: MAGIS, member_count: int = 0) -> MAGISOut:
    return MAGISOut(
        id=m.id,
        name=m.name,
        parent_id=m.parent_id,
        adam_id=m.adam_id,
        child_count=len(m.children),
        member_count=member_count,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# -- endpoints --------------------------------------------------------------


@router.get("/magis", response_model=list[MAGISOut])
def list_magis(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> list[MAGISOut]:
    """Return every MAGIS row (the MAGI Societies tree)."""
    magis = session.scalars(
        select(MAGIS)
        .options(selectinload(MAGIS.children))
        .order_by(MAGIS.name.asc())
    ).all()

    member_counts: dict[int, int] = {}
    if magis:
        from magi.agent.db import MAGIC
        counts = session.execute(
            select(MAGIC.magis_id, func.count())
            .where(MAGIC.magis_id.in_([m.id for m in magis]))
            .group_by(MAGIC.magis_id)
        ).all()
        member_counts = {mid: cnt for mid, cnt in counts}

    return [_serialize_magis(m, member_counts.get(m.id, 0)) for m in magis]


@router.post("/magis", response_model=MAGISOut, status_code=201)
def create_magis(
    payload: MAGISCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGISOut:
    """Create a new MAGIS group."""
    parent = session.get(MAGIS, payload.parent_id)
    if parent is None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.parent_magis_not_found",
            detail=f"parent MAGIS id {payload.parent_id} not found",
        )

    existing = session.scalar(
        select(MAGIS).where(MAGIS.name == payload.name)
    )
    if existing is not None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magis_name_duplicate",
            detail=f"MAGIS name {payload.name!r} already exists",
        )

    magis = MAGIS(
        name=payload.name,
        parent_id=payload.parent_id,
    )
    session.add(magis)
    session.commit()
    session.refresh(magis)
    logger.info("created MAGIS id=%d name=%r parent_id=%s", magis.id, magis.name, magis.parent_id)
    return _serialize_magis(magis, member_count=0)


def _count_members(session: Session, magis_id: int) -> int:
    """Return the number of MAGIC rows in this MAGIS."""
    from magi.agent.db import MAGIC
    return session.scalar(
        select(func.count()).select_from(MAGIC).where(MAGIC.magis_id == magis_id)
    ) or 0


@router.get("/magis/{magis_id}", response_model=MAGISOut)
def get_magis(
    magis_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGISOut:
    magis = session.get(MAGIS, magis_id)
    if magis is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magis",
            detail="MAGIS not found",
        )
    session.refresh(magis, ["children"])
    return _serialize_magis(magis, member_count=_count_members(session, magis_id))


@router.patch("/magis/{magis_id}", response_model=MAGISOut)
def update_magis(
    magis_id: int,
    payload: MAGISUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGISOut:
    """Update a MAGIS group's metadata."""
    magis = session.get(MAGIS, magis_id)
    if magis is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magis",
            detail="MAGIS not found",
        )

    if payload.name is not None and payload.name != magis.name:
        existing = session.scalar(
            select(MAGIS).where(MAGIS.name == payload.name)
        )
        if existing is not None and existing.id != magis_id:
            raise MagiHTTPException(
                status_code=400,
                code="validation.magis_name_duplicate",
                detail=f"MAGIS name {payload.name!r} already exists",
            )
        magis.name = payload.name

    if "parent_id" in payload.model_fields_set:
        new_parent = payload.parent_id
        if new_parent == magis_id:
            raise MagiHTTPException(
                status_code=400,
                code="validation.magis_self_parent",
                detail="A MAGIS cannot be its own parent",
            )
        if new_parent is not None:
            target = session.get(MAGIS, new_parent)
            if target is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.parent_magis_not_found",
                    detail=f"parent MAGIS id {new_parent} not found",
                )
            cursor = target
            while cursor is not None:
                if cursor.id == magis_id:
                    raise MagiHTTPException(
                        status_code=400,
                        code="validation.magis_cycle",
                        detail=(
                            f"Cannot move MAGIS {magis_id!r} under "
                            f"{new_parent!r} — that would create a "
                            f"parent cycle"
                        ),
                    )
                cursor = cursor.parent_id is not None and session.get(MAGIS, cursor.parent_id)
        magis.parent_id = new_parent

    if "adam_id" in payload.model_fields_set:
        new_adam = payload.adam_id
        if new_adam is not None:
            adam_magic = session.get(MAGIC, new_adam)
            if adam_magic is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.adam_magic_not_found",
                    detail=f"adam MAGIC id {new_adam} not found",
                )
            if adam_magic.magic_position != "adam":
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.adam_must_be_adam",
                    detail=(
                        f"MAGIC id {new_adam} has magic_position="
                        f"{adam_magic.magic_position!r}, must be 'adam'"
                    ),
                )
        magis.adam_id = new_adam

    session.commit()
    session.refresh(magis, ["children"])
    return _serialize_magis(magis, member_count=_count_members(session, magis_id))


@router.delete("/magis/{magis_id}", status_code=204)
def delete_magis(
    magis_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Delete a MAGIS group."""
    magis = session.get(MAGIS, magis_id)
    if magis is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magis",
            detail="MAGIS not found",
        )

    reparent_to = magis.parent_id

    from sqlalchemy import update

    session.execute(
        update(MAGIS)
        .where(MAGIS.parent_id == magis_id)
        .values(parent_id=reparent_to)
    )

    session.delete(magis)
    session.commit()
    logger.info("deleted MAGIS id=%d name=%r", magis_id, magis.name)
    return Response(status_code=204)
