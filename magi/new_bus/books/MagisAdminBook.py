"""MagisAdminBook — MAGIS 管理员簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Admin:
    magis_id: int
    magic_id: int


class _AdminRow(Base):
    __tablename__ = "magis_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(Integer, nullable=False)
    magic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class MagisAdminBook(BaseBook[_AdminRow, Admin]):
    model_cls = _AdminRow
    dto_cls = Admin

    def list_by_magis(self, *, magis_id: int) -> list[Admin]:
        with self._session() as s:
            rows = s.scalars(
                select(_AdminRow).where(_AdminRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def is_admin(self, *, magis_id: int, magic_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_AdminRow).where(
                    _AdminRow.magis_id == magis_id,
                    _AdminRow.magic_id == magic_id,
                )
            )
            return row is not None
