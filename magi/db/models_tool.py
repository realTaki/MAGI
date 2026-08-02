"""ORM table ``tools`` — the agent-visible tool registry.

Stores the LLM-facing metadata (name, description, input_schema,
allowed_roles) for every tool the MAGI can invoke.  The tool worker
owns the rows: it upserts on startup and whenever tool code changes.
The agent reads schemas from here; it never imports a tool class.

Schema is deliberately a flat key-value shape: each tool is one row
with the Anthropic-shaped ``input_schema`` stored as JSON.  There is
no ``Tool`` Python object stored — the worker keeps its own
in-memory instance dict for ``tool.run()``, and this table is the
LLM's menu.
"""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.db.base import Base, utcnow_naive


class ToolRegistry(Base):
    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    # ``None`` means "no role restriction".  A non-None list means
    # only callers whose role is in the list may see/use the tool.
    allowed_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="builtin")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    def to_llm_schema(self) -> dict:
        """Render this row into the dict the LLM provider expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
