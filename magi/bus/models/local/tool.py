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

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class ToolDefinitionRecord(Base):
    """Durable Tool Catalog row owned by :mod:`magi.bus`.

    It is an internal persistence model. Callers use the immutable
    ``magi.bus.protocols.ToolDefinition`` DTO via the catalog service.
    """

    __tablename__ = "tool_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column("input_schema_json", JSON, nullable=False)
    allowed_roles: Mapped[list | None] = mapped_column("allowed_roles_json", JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    implementation_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (
        UniqueConstraint("source", "name", name="uq_tool_definitions_source_name"),
    )


class ToolCatalogState(Base):
    """Single-row monotonic revision and content hash for the catalog."""

    __tablename__ = "tool_catalog_state"

    singleton_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
