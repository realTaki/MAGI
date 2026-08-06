"""MagisMembershipBook + MagisRoleBook — ``magis_memberships`` + ``magis_roles``.

Schema mirrors the old bus's tables.  Each ``MAGIS`` has at least
two reserved roles (``ADAM`` and ``EVA``) created by
:meth:`ensure_default_roles` (a caller-side helper, not a Book method
— Books are pure CRUD).
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

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "ADAM": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVA": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
}


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class MagisMembership:
    id: int
    magis_id: int
    magic_id: int
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
    magic_id: Mapped[int] = mapped_column(
        ForeignKey("magic.id", ondelete="CASCADE"), nullable=False, index=True
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

    __table_args__ = (
        UniqueConstraint("magic_id", name="uq_magis_memberships_magic"),
    )


# -- Books ---------------------------------------------------------------


class MagisRoleBook(BaseBook[_MagisRoleRow, MagisRole]):
    model_cls = _MagisRoleRow
    dto_cls = MagisRole

    def get(self, *, role_id: int) -> MagisRole | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(_MagisRoleRow.id == role_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_magis(self, *, magis_id: int) -> list[MagisRole]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_MagisRoleRow)
                .where(_MagisRoleRow.magis_id == magis_id)
                .order_by(_MagisRoleRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def find(self, *, magis_id: int, name: str) -> MagisRole | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(
                    _MagisRoleRow.magis_id == magis_id,
                    _MagisRoleRow.name == name,
                )
            )
            return self._row_to_dto(row) if row else None

    def add(self, *, magis_id: int, name: str,
            instruction: str = "", is_reserved: bool = False) -> MagisRole:
        with self._factory.session() as s:
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

    def get(self, *, membership_id: int) -> MagisMembership | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.id == membership_id)
            )
            return self._row_to_dto(row) if row else None

    def find_for_magic(self, *, magic_id: int) -> MagisMembership | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.magic_id == magic_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_magis(self, *, magis_id: int) -> list[MagisMembership]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, magis_id: int, magic_id: int, role_id: int) -> MagisMembership:
        with self._factory.session() as s:
            row = _MagisMembershipRow(
                magis_id=magis_id, magic_id=magic_id, role_id=role_id
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def remove(self, *, magic_id: int) -> bool:
        with self._factory.session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.magic_id == magic_id)
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
