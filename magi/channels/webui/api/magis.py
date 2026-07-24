"""Magi CRUD API — the "智能体管理 / magis 管理" surface.

Each row is a :class:`Magi` (a MAGI runtime agent bound to
a :class:`MAGIC` via ``magic_id``). ``magic_position`` is
one of ``"adam"`` (the manager — exactly one per ``MAGIC``)
or ``"eve"`` (a worker — N per ``MAGIC``).

All routes require the caller to be signed in **and** an
admin (an ``Employee`` row with ``role='admin'``); both
checks run via the shared :func:`admin_gate` dependency in
:meth:`magi.channels.webui.api.employees.admin_gate` (re-
imported here to avoid a circular import — the employees
router exposes the gate as a module-level callable).

Notes
-----

- ``provider`` / ``api_key`` are write-only on PATCH (the
  key is never read back; only a ``last4`` is returned in
  GET responses, mirroring :class:`Employee`).
- The seeded root MAGIC is guaranteed to have one
  ``Magi(magic_position='adam')`` row at boot — see
  :func:`magi.agent.db.engine._seed_default_root`.
"""

from __future__ import annotations

import logging

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from magi.agent.db import (
    MAGIC,
    Magi,
    get_session,
)
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.magis")

router = APIRouter(tags=["magis"])


# -- admin gate -------------------------------------------------------------
#
# Reuse the employees router's gate so the admin check lives
# in one place. The employees router imports this module
# only indirectly (via app.py router order) so there's no
# circular import at module-load time.


def _admin_gate(request: Request) -> str:
    """FastAPI dependency — verify the caller is an admin.

    Delegates to :func:`magi.channels.webui.api.employees.admin_gate`
    which reads the ``magi_session`` cookie (D.24 carries the
    uid, not a chat id) and confirms ``Employee.role == 'admin'``.
    """
    from magi.channels.webui.api.employees import admin_gate

    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


# -- response / payload shapes ----------------------------------------------

_MAGI_POSITIONS: tuple[str, ...] = ("adam", "eve")


def _mask_key(raw: str | None) -> tuple[bool, str | None]:
    """Return ``(is_set, last4_or_None)`` from a stored key.

    Mirrors :func:`magi.channels.webui.api.employees._mask_key`
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
    name: str
    magic_position: str | None = None


class MagiOut(BaseModel):
    id: int
    magic_id: int
    magic_position: str
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    created_at: str
    updated_at: str


class MagiCreate(BaseModel):
    magic_id: int = Field(ge=1)
    magic_position: str = Field(min_length=1, max_length=16)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)


class MagiUpdate(BaseModel):
    magic_position: Optional[str] = Field(default=None, max_length=16)
    provider: Optional[str] = Field(default=None, max_length=64)
    api_key: Optional[str] = Field(default=None, max_length=256)


def _serialize(m: Magi) -> MagiOut:
    is_set, last4 = _mask_key(m.api_key)
    return MagiOut(
        id=m.id,
        magic_id=m.magic_id,
        magic_position=m.magic_position,
        provider=m.provider,
        api_key_set=is_set,
        api_key_last4=last4,
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
    return [_serialize(m) for m in rows]


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
    if session.get(MAGIC, payload.magic_id) is None:
        raise MagiHTTPException(
            status_code=400,
            code="validation.magic_id_not_found",
            detail=f"magic_id {payload.magic_id} not found",
        )

    magi = Magi(
        magic_id=payload.magic_id,
        magic_position=payload.magic_position,
        provider=payload.provider,
        api_key=payload.api_key,
    )
    session.add(magi)
    session.commit()
    session.refresh(magi)
    return _serialize(magi)


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
    return _serialize(magi)


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
    return _serialize(magi)


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
    session.delete(magi)
    session.commit()
    return Response(status_code=204)


# -- MAGIC tree list (used by the frontend "MAGI 团队" picker) -------------

@router.get("/magics", response_model=list["MAGICOut"])
def list_magics(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> list["MAGICOut"]:
    """Return every ``MAGIC`` row (the MAGI team tree).

    Sorted by name for stable display order; the frontend
    groups by ``parent_id`` and indents to show the hierarchy.
    """
    magics = session.scalars(
        select(MAGIC)
        .options(selectinload(MAGIC.children))
        .order_by(MAGIC.name.asc())
    ).all()
    return [_serialize_magic(m) for m in magics]


class MAGICOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    adam_id: int | None
    child_count: int = 0
    created_at: str
    updated_at: str


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