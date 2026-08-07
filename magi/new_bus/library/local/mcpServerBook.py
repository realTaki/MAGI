"""McpServerBook — operator-configured MCP server rows.

Schema mirrors the old bus's ``mcp_servers`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class McpServer:
    id: int
    name: str
    transport: str
    config: dict[str, Any]
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None


# -- internal ORM --------------------------------------------------------


class _McpServerRow(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book ----------------------------------------------------------------


class McpServerBook(BaseBook[_McpServerRow, McpServer]):
    model_cls = _McpServerRow
    dto_cls = McpServer

    def get(self, *, server_id: int) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.id == server_id)
            )
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[McpServer]:
        with self._session() as s:
            rows = s.scalars(
                select(_McpServerRow).order_by(_McpServerRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self) -> list[McpServer]:
        with self._session() as s:
            rows = s.scalars(
                select(_McpServerRow)
                .where(_McpServerRow.enabled == 1)
                .order_by(_McpServerRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, name: str, transport: str,
            config: dict[str, Any] | None = None,
            enabled: bool = True) -> McpServer:
        with self._session() as s:
            row = _McpServerRow(
                name=name, transport=transport,
                config=config or {}, enabled=1 if enabled else 0,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update(self, *, server_id: int,
               transport: str | None = None,
               config: dict[str, Any] | None = None,
               enabled: bool | None = None) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.id == server_id)
            )
            if row is None:
                return
            if transport is not None:
                row.transport = transport
            if config is not None:
                row.config = config
            if enabled is not None:
                row.enabled = 1 if enabled else 0
            s.commit()

    def delete(self, *, server_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.id == server_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["McpServer", "McpServerBook", "_McpServerRow"]
