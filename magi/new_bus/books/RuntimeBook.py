"""RuntimeBook — 运行时状态簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Runtime:
    runtime_id: str
    status: str
    port: int | None = None


class _RuntimeRow(Base):
    __tablename__ = "control_runtime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class RuntimeBook(BaseBook[_RuntimeRow, Runtime]):
    model_cls = _RuntimeRow
    dto_cls = Runtime

    def get(self, *, runtime_id: str) -> Runtime | None:
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Runtime]:
        with self._session() as s:
            rows = s.scalars(
                select(_RuntimeRow).order_by(_RuntimeRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]
