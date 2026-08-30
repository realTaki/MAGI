"""ToolsBook — durable Agent-visible tool catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


class ToolSource(StrEnum):
    """Where a catalogued tool came from."""

    BUILTIN = "builtin"
    MCP = "mcp"
    MANUAL = "manual"


@dataclass(kw_only=True)
class Tool(BaseRecord):
    """One Agent-visible tool definition.

    The executable class stays in ``tools``. This row is the catalog entry
    the Agent menu and later Tool Jobs read. Future Tool Jobs own upsert and
    role filtering; this Book owns only the durable row shape.
    """

    name: str
    description: str = ""
    source: ToolSource = ToolSource.BUILTIN
    input_schema: dict[str, Any] = field(default_factory=dict)
    allowed_roles: list[str] = field(default_factory=list)
    enabled: bool = True


class ToolRow(BaseRecordMixin):
    __tablename__ = "books_tools"
    __table_args__ = (
        UniqueConstraint("name", name="uq_books_tools_name"),
        Index("ix_books_tools_source", "source"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default=ToolSource.BUILTIN.value)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    allowed_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ToolsBook(BaseBook[Tool]):
    """Internal collection for :class:`Tool` catalog rows."""

    record_cls = Tool
    row_cls = ToolRow
