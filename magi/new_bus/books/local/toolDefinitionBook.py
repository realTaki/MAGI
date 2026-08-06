"""ToolCatalogStateBook + ToolDefinitionBook — durable Tool Catalog.

Two tables:
- ``tool_catalog_state`` — singleton (id=1) holding the monotonic
  catalog revision + snapshot hash
- ``tool_definitions``   — one row per catalog tool

Schema mirrors the old bus's ``tool_catalog_state`` + ``tool_definitions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    BigInteger,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    id: int
    name: str
    spec_json: str
    spec_dict: dict[str, Any] | None = None
    revision: int = 0
    enabled: int = 1
    description: str | None = None
    source: str = "manual"


# -- internal ORM --------------------------------------------------------


class _ToolCatalogStateRow(Base):
    __tablename__ = "tool_catalog_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class _ToolDefinitionRow(Base):
    __tablename__ = "tool_definitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    spec_dict: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    __table_args__ = (UniqueConstraint("name", name="uq_tool_definitions_name"),)


# -- Books ---------------------------------------------------------------


class ToolCatalogStateBook(BaseBook[_ToolCatalogStateRow, ToolCatalogState]):
    model_cls = _ToolCatalogStateRow
    dto_cls = ToolCatalogState

    def get(self) -> ToolCatalogState | None:
        with self._factory.session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            return self._row_to_dto(row) if row else None

    def bump(self, *, revision: int, snapshot_hash: str) -> ToolCatalogState:
        with self._factory.session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            if row is None:
                row = _ToolCatalogStateRow(
                    revision=revision, snapshot_hash=snapshot_hash
                )
                s.add(row)
            else:
                row.revision = revision
                row.snapshot_hash = snapshot_hash
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


class ToolDefinitionBook(BaseBook[_ToolDefinitionRow, ToolDefinition]):
    model_cls = _ToolDefinitionRow
    dto_cls = ToolDefinition

    def get(self, *, tool_id: int) -> ToolDefinition | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.id == tool_id)
            )
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> ToolDefinition | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[ToolDefinition]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ToolDefinitionRow).order_by(_ToolDefinitionRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self) -> list[ToolDefinition]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ToolDefinitionRow)
                .where(_ToolDefinitionRow.enabled == 1)
                .order_by(_ToolDefinitionRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def upsert(self, *, name: str, spec_json: str, revision: int = 0,
               description: str | None = None, source: str = "manual",
               spec_dict: str | None = None) -> ToolDefinition:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            if row is None:
                row = _ToolDefinitionRow(
                    name=name, spec_json=spec_json, revision=revision,
                    description=description, source=source, spec_dict=spec_dict,
                )
                s.add(row)
            else:
                row.spec_json = spec_json
                row.revision = revision
                row.description = description
                row.source = source
                row.spec_dict = spec_dict
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


__all__ = [
    "ToolCatalogState",
    "ToolDefinition",
    "ToolCatalogStateBook",
    "ToolDefinitionBook",
    "_ToolCatalogStateRow",
    "_ToolDefinitionRow",
]
