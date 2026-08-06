"""MagisMembershipBook — 成员关系簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Membership:
    magis_id: int
    magic_id: int
    role: str


class _MembershipRow(Base):
    __tablename__ = "magis_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(Integer, nullable=False)
    magic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class MagisMembershipBook(BaseBook[_MembershipRow, Membership]):
    model_cls = _MembershipRow
    dto_cls = Membership

    def list_by_magis(self, *, magis_id: int) -> list[Membership]:
        with self._session() as s:
            rows = s.scalars(
                select(_MembershipRow)
                .where(_MembershipRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_by_magic(self, *, magic_id: int) -> list[Membership]:
        with self._session() as s:
            rows = s.scalars(
                select(_MembershipRow)
                .where(_MembershipRow.magic_id == magic_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]
