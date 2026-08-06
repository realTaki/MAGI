"""MemoryBook — 记忆簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Memory:
    memory_id: str
    owner_id: str
    kind: str
    content: str


# -- internal ORM ----------------------------------------------------------

class _MemoryRow(Base):
    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


# -- Book ------------------------------------------------------------------

class MemoryBook(BaseBook[_MemoryRow, Memory]):
    model_cls = _MemoryRow
    dto_cls = Memory

    def list_by_owner(self, *, owner_id: str) -> list[Memory]:
        with self._session() as s:
            rows = s.scalars(
                select(_MemoryRow)
                .where(_MemoryRow.owner_id == owner_id)
                .order_by(_MemoryRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def get(self, *, memory_id: str) -> Memory | None:
        with self._session() as s:
            row = s.scalar(
                select(_MemoryRow).where(_MemoryRow.memory_id == memory_id)
            )
            return self._row_to_dto(row) if row else None
