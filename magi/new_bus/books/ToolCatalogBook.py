"""ToolCatalogBook — 工具目录簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_id: str
    name: str
    description: str | None = None
    source: str | None = None
    enabled: bool = True


class _ToolRow(Base):
    __tablename__ = "tool_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class ToolCatalogBook(BaseBook[_ToolRow, ToolDefinition]):
    model_cls = _ToolRow
    dto_cls = ToolDefinition

    def get(self, *, tool_id: str) -> ToolDefinition | None:
        with self._session() as s:
            row = s.scalar(
                select(_ToolRow).where(_ToolRow.tool_id == tool_id)
            )
            return self._row_to_dto(row) if row else None

    def list_enabled(self) -> list[ToolDefinition]:
        with self._session() as s:
            rows = s.scalars(
                select(_ToolRow)
                .where(_ToolRow.enabled == True)
                .order_by(_ToolRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_all(self) -> list[ToolDefinition]:
        with self._session() as s:
            rows = s.scalars(
                select(_ToolRow).order_by(_ToolRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
