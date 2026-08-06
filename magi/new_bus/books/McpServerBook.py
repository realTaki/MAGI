"""McpServerBook — MCP 服务器簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class McpServer:
    server_id: str
    name: str
    url: str | None = None
    enabled: bool = True


class _McpServerRow(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class McpServerBook(BaseBook[_McpServerRow, McpServer]):
    model_cls = _McpServerRow
    dto_cls = McpServer

    def get(self, *, server_id: str) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.server_id == server_id)
            )
            return self._row_to_dto(row) if row else None

    def list_enabled(self) -> list[McpServer]:
        with self._session() as s:
            rows = s.scalars(
                select(_McpServerRow)
                .where(_McpServerRow.enabled == True)
                .order_by(_McpServerRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
