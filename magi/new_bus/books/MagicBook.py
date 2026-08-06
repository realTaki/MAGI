"""MagicBook — MAGI 代理簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Magic:
    magic_id: int
    name: str
    provider: str | None = None


class _MagicRow(Base):
    __tablename__ = "magic"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    magic_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class MagicBook(BaseBook[_MagicRow, Magic]):
    model_cls = _MagicRow
    dto_cls = Magic

    def get(self, *, magic_id: int) -> Magic | None:
        with self._session() as s:
            row = s.get(_MagicRow, magic_id)
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Magic]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagicRow).order_by(_MagicRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
