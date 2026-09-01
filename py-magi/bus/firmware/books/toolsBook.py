"""ToolsBook — durable Agent-visible tool catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Boolean, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


@dataclass(frozen=True)
class LLMTool:
    """The LLM-facing portion of a catalogued tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(kw_only=True)
class Tool(BaseRecord):
    """One Agent-visible tool definition.

    The executable class stays in ``tools``. This row is the catalog entry
    the Agent menu and Tool Jobs read. Tool Jobs own upsert; this Book owns
    only the durable row shape.
    """

    name: str
    definition: LLMTool
    enabled: bool = True


class ToolRow(BaseRecordMixin):
    __tablename__ = "books_tools"
    __table_args__ = (UniqueConstraint("name", name="uq_books_tools_name"),)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ToolsBook(BaseBook[Tool]):
    """Internal collection for :class:`Tool` catalog rows."""

    record_cls = Tool
    row_cls = ToolRow

    def get(self, name: str) -> Tool | None:  # type: ignore[override]
        with self._session() as session:
            row = session.scalar(select(ToolRow).where(ToolRow.name == name))
            return None if row is None else Tool.from_row(row)

    def upsert(self, record: Tool) -> int:
        existing = self.get(record.name)
        if existing is None:
            return self.add(record)
        existing.definition = record.definition
        existing.enabled = record.enabled
        super().update(existing)
        return existing.id
