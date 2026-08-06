"""ControlPlaneBook — 控制面板簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Operator:
    operator_id: str
    name: str


class _OperatorRow(Base):
    __tablename__ = "control_operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ControlPlaneBook(BaseBook[_OperatorRow, Operator]):
    model_cls = _OperatorRow
    dto_cls = Operator

    def get(self, *, operator_id: str) -> Operator | None:
        with self._session() as s:
            row = s.scalar(
                select(_OperatorRow).where(_OperatorRow.operator_id == operator_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Operator]:
        with self._session() as s:
            rows = s.scalars(
                select(_OperatorRow).order_by(_OperatorRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
