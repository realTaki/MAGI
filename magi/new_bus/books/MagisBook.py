"""MagisBook — MAGI Society 树簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Magis:
    magis_id: int
    name: str
    parent_id: int | None = None
    adam_id: int | None = None


class _MagisRow(Base):
    __tablename__ = "magis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adam_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class MagisBook(BaseBook[_MagisRow, Magis]):
    model_cls = _MagisRow
    dto_cls = Magis

    def get(self, *, magis_id: int) -> Magis | None:
        with self._session() as s:
            row = s.get(_MagisRow, magis_id)
            return self._row_to_dto(row) if row else None

    def get_root(self) -> Magis | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRow).where(_MagisRow.parent_id.is_(None)).limit(1)
            )
            return self._row_to_dto(row) if row else None

    def list_children(self, *, parent_id: int) -> list[Magis]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisRow)
                .where(_MagisRow.parent_id == parent_id)
                .order_by(_MagisRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_all(self) -> list[Magis]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisRow).order_by(_MagisRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
