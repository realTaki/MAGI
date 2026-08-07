"""MagisMembershipBook + MagisRoleBook — register MAGIs under a MAGIS.

Two tables, both scoped to one parent ``MAGIS``:

- ``magis_memberships`` — one row per MAGI instance.  A row binds
  ``(magis_id, role_id)`` and the row's own ``id`` *is* the per-MAGI
  identity (no separate ``magic`` table — see FK note below).
- ``magis_roles``     — the role vocabulary a parent MAGIS offers.
  ``MagisRoleBook.add`` creates the reserved roles (``ADAM``,
  ``EVA``); custom roles are also allowed.

Schema mirrors the old bus's tables.  Per-MAGI runtime config
(display ``name``, ``instruction``, LLM ``provider`` / ``api_key``)
does NOT live here — it lives in the LOCAL :class:`SettingBook`
under the ``SettingBook.KNOWN_KEYS`` keys.  This Book only tracks
the per-MAGI identity + role binding.

FKs that target a per-MAGI identity (``magis.adam_id``,
``eva_runtimes.magic_id``) all point at ``magis_memberships.id``.

Query keys
----------

- ``magis_id`` — identifies a MAGIS (used for ``list_for_magis``).
- ``magi_id``  — identifies a single MAGI under a MAGIS (this is
  what the old ``magic.id`` semantic became; we drop the ``c`` to
  match the singular ``MAGI``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "ADAM": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVA": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
}


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class MagisRole:
    id: int
    magis_id: int
    name: str
    instruction: str = ""
    is_reserved: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MagisMembership:
    id: int
    magis_id: int
    role_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _MagisRoleRow(Base):
    __tablename__ = "magis_roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("magis_id", "name", name="uq_magis_roles_magis_name"),
    )


class _MagisMembershipRow(Base):
    __tablename__ = "magis_memberships"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("magis_roles.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Books ---------------------------------------------------------------


class MagisRoleBook(BaseBook[_MagisRoleRow, MagisRole]):
    model_cls = _MagisRoleRow
    dto_cls = MagisRole

    def get(self, *, role_id: int) -> MagisRole | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(_MagisRoleRow.id == role_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_magis(self, *, magis_id: int) -> list[MagisRole]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisRoleRow)
                .where(_MagisRoleRow.magis_id == magis_id)
                .order_by(_MagisRoleRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def find(self, *, magis_id: int, name: str) -> MagisRole | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(
                    _MagisRoleRow.magis_id == magis_id,
                    _MagisRoleRow.name == name,
                )
            )
            return self._row_to_dto(row) if row else None

    def add(self, *, magis_id: int, name: str,
            instruction: str = "", is_reserved: bool = False) -> MagisRole:
        with self._session() as s:
            row = _MagisRoleRow(
                magis_id=magis_id, name=name,
                instruction=instruction, is_reserved=is_reserved,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


class MagisMembershipBook(BaseBook[_MagisMembershipRow, MagisMembership]):
    model_cls = _MagisMembershipRow
    dto_cls = MagisMembership

    def get(self, *, magi_id: int) -> MagisMembership | None:
        """Look up a single MAGI under any MAGIS by its per-MAGI identity.

        ``magi_id`` is the ``magis_memberships.id`` (the row's own
        PK, which is what used to be ``magic.id`` before the
        ``magic`` table was retired).
        """
        with self._session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.id == magi_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_magis(self, *, magis_id: int) -> list[MagisMembership]:
        """All MAGIs registered under a given parent MAGIS."""
        with self._session() as s:
            rows = s.scalars(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, magis_id: int, role_id: int) -> MagisMembership:
        """Register a new MAGI under ``magis_id`` with ``role_id``.

        The MAGI's own identity is assigned by the DB and comes back
        as ``dto.id`` — keep that id for later lookup and for use as
        a FK target from elsewhere (``magis.adam_id``,
        ``eva_runtimes.magic_id``).
        """
        with self._session() as s:
            row = _MagisMembershipRow(magis_id=magis_id, role_id=role_id)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def remove(self, *, magi_id: int) -> bool:
        """Unregister a MAGI by its per-MAGI identity."""
        with self._session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.id == magi_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = [
    "MagisRole",
    "MagisMembership",
    "MagisRoleBook",
    "MagisMembershipBook",
    "_MagisRoleRow",
    "_MagisMembershipRow",
    "RESERVED_ROLE_NAMES",
    "DEFAULT_ROLE_INSTRUCTIONS",
]