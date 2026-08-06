"""EvaRuntimeBook — EVA 运行时簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class EvaRuntime:
    runtime_id: str
    magis_id: int
    magic_id: int
    status: str


class _EvaRuntimeRow(Base):
    __tablename__ = "eva_runtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    magis_id: Mapped[int] = mapped_column(Integer, nullable=False)
    magic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class EvaRuntimeBook(BaseBook[_EvaRuntimeRow, EvaRuntime]):
    model_cls = _EvaRuntimeRow
    dto_cls = EvaRuntime

    def get(self, *, runtime_id: str) -> EvaRuntime | None:
        with self._session() as s:
            row = s.scalar(
                select(_EvaRuntimeRow).where(_EvaRuntimeRow.runtime_id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def list_by_magis(self, *, magis_id: int) -> list[EvaRuntime]:
        with self._session() as s:
            rows = s.scalars(
                select(_EvaRuntimeRow)
                .where(_EvaRuntimeRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_by_magic(self, *, magic_id: int) -> list[EvaRuntime]:
        with self._session() as s:
            rows = s.scalars(
                select(_EvaRuntimeRow)
                .where(_EvaRuntimeRow.magic_id == magic_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]
