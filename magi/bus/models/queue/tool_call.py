"""ORM table: tool_calls (within-run tool call record with ordinal + result)."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus._persistence.base import Base, utcnow_naive


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_call_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Within-run ordinal assigned at ToolCall write time.
    ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordered_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    completed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_tool_calls_run_created", "run_id", "created_at"),
        Index("ix_tool_calls_run_ordinal", "run_id", "ordinal"),
    )
