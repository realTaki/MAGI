"""MAGIC CRUD API — the "MAGI 团队 / MAGIC (MAGI Council)" surface.

Each row is a :class:`MAGIC` (a MAGI team / council) that forms
a tree-structured org. Each team has exactly one ``adam``
Magi (the manager), enforced at the application layer (v0
doesn't have a partial UNIQUE index yet — F1 adds it).

All routes require the caller to be signed in **and** an
admin (an ``Employee`` row with ``role='admin'``); the
``admin_gate`` dependency is shared from
:mod:`magi.channels.webui.api.employees`.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from magi.agent.db import (
    MAGIC,
    Magi,
    get_session,
)
from magi.agent.db.base import utcnow_naive
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magics")

router = APIRouter(tags=["magics"])


# -- admin gate -------------------------------------------------------------
#
# Reuse the employees router's gate so the admin check lives
# in one place.


def _admin_gate(request: Request) -> str:
    from magi.channels.webui.api.employees import admin_gate

    return admin_gate(request)


from typing import Annotated

AdminGate = Annotated[str, Depends(_admin_gate)]


# -- response / payload shapes ----------------------------------------------


class MAGICOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    adam_id: int | None
    child_count: int = 0
    created_at: str
    updated_at: str


class MAGICCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = Field(default=None, ge=1)


class MAGICUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = Field(default=None, ge=1)
    adam_id: int | None = Field(default=None, ge=1)


def _serialize_magic(m: MAGIC) -> MAGICOut:
    return MAGICOut(
        id=m.id,
        name=m.name,
        parent_id=m.parent_id,
        adam_id=m.adam_id,
        child_count=len(m.children),
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# -- endpoints --------------------------------------------------------------


@router.get("/magics", response_model=list[MAGICOut])
def list_magics(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> list[MAGICOut]:
    """Return every MAGIC row (the MAGI team tree).

    Sorted by name for stable display order; the frontend
    groups by ``parent_id`` to render the hierarchy.
    """
    magics = session.scalars(
        select(MAGIC)
        .options(selectinload(MAGIC.children))
        .order_by(MAGIC.name.asc())
    ).all()
    return [_serialize_magic(m) for m in magics]


@router.post("/magics", response_model=MAGICOut, status_code=201)
def create_magic(
    payload: MAGICCreate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGICOut:
    """Create a new MAGIC team.

    ``name`` must be unique across the table (enforced by the
    DB unique constraint). ``parent_id``, when set, must
    reference an existing MAGIC row.
    """
    # Validate parent_id when provided.
    if payload.parent_id is not None:
        parent = session.get(MAGIC, payload.parent_id)
        if parent is None:
            raise MagiHTTPException(
                status_code=400,
                code="validation.parent_magic_not_found",
                detail=f"parent MAGIC id {payload.parent_id} not found",
            )

    # Check name uniqueness explicitly so we can return a
    # friendly error before hitting the DB constraint.
    existing = session.scalar(
        select(MAGIC).where(MAGIC.name == payload.name)
    )
    if existing is not None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magic_name_duplicate",
            detail=f"MAGIC name {payload.name!r} already exists",
        )

    magic = MAGIC(
        name=payload.name,
        parent_id=payload.parent_id,
    )
    session.add(magic)
    session.commit()
    session.refresh(magic)
    logger.info("created MAGIC id=%d name=%r parent_id=%s", magic.id, magic.name, magic.parent_id)
    return _serialize_magic(magic)


@router.get("/magics/{magic_id}", response_model=MAGICOut)
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
    # Load children for child_count.
    session.refresh(magic, ["children"])
    return _serialize_magic(magic)


@router.patch("/magics/{magic_id}", response_model=MAGICOut)
def update_magic(
    magic_id: int,
    payload: MAGICUpdate,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> MAGICOut:
    """Update a MAGIC team's metadata.

    ``name`` — rename (must stay unique).
    ``parent_id`` — move this team under a different parent
    (validates the target exists and isn't a descendant to
    avoid cycles).
    ``adam_id`` — assign a different Magi as this team's adam
    (validates the Magi exists and has ``magic_position='adam'``).
    """
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magic",
            detail="MAGIC not found",
        )

    if payload.name is not None and payload.name != magic.name:
        # Check uniqueness.
        existing = session.scalar(
            select(MAGIC).where(MAGIC.name == payload.name)
        )
        if existing is not None and existing.id != magic_id:
            raise MagiHTTPException(
                status_code=400,
                code="validation.magic_name_duplicate",
                detail=f"MAGIC name {payload.name!r} already exists",
            )
        magic.name = payload.name

    if "parent_id" in payload.model_fields_set:
        new_parent = payload.parent_id
        if new_parent == magic_id:
            raise MagiHTTPException(
                status_code=400,
                code="validation.magic_self_parent",
                detail="A MAGIC cannot be its own parent",
            )
        if new_parent is not None:
            target = session.get(MAGIC, new_parent)
            if target is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.parent_magic_not_found",
                    detail=f"parent MAGIC id {new_parent} not found",
                )
            # Cycle check: walk up the tree; if we hit
            # magic_id, the move would create a cycle.
            cursor = target
            while cursor is not None:
                if cursor.id == magic_id:
                    raise MagiHTTPException(
                        status_code=400,
                        code="validation.magic_cycle",
                        detail=(
                            f"Cannot move MAGIC {magic_id!r} under "
                            f"{new_parent!r} — that would create a " 
                            f"parent cycle"
                        ),
                    )
                cursor = cursor.parent_id is not None and session.get(MAGIC, cursor.parent_id)
        magic.parent_id = new_parent

    if "adam_id" in payload.model_fields_set:
        new_adam = payload.adam_id
        if new_adam is not None:
            adam_magi = session.get(Magi, new_adam)
            if adam_magi is None:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.adam_magi_not_found",
                    detail=f"adam Magi id {new_adam} not found",
                )
            if adam_magi.magic_position != "adam":
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.adam_must_be_adam",
                    detail=(
                        f"Magi id {new_adam} has magic_position="
                        f"{adam_magi.magic_position!r}, must be 'adam'"
                    ),
                )
        magic.adam_id = new_adam

    session.commit()
    session.refresh(magic, ["children"])
    return _serialize_magic(magic)


@router.delete("/magics/{magic_id}", status_code=204)
def delete_magic(
    magic_id: int,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Delete a MAGIC team.

    Children (sub-teams with ``parent_id == magic_id``) are
    reassigned to the deleted team's parent before the delete
    (cascade=None on the FK means SQLite won't cascade-delete
    them; we reparent explicitly to avoid orphaning).
    Magi rows referencing this MAGIC via ``magic_id`` are
    NOT deleted — they become orphaned (magic_id FK is CASCADE
    so they would be deleted by the DB).
    """
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.magic",
            detail="MAGIC not found",
        )

    # Reparent children to the deleted team's parent.
    children = session.scalars(
        select(MAGIC).where(MAGIC.parent_id == magic_id)
    ).all()
    for child in children:
        child.parent_id = magic.parent_id

    session.delete(magic)
    session.commit()
    logger.info("deleted MAGIC id=%d name=%r", magic_id, magic.name)
    return Response(status_code=204)
