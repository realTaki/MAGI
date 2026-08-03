"""ORM table: agent_runs (one row per agent turn, the run state machine)."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.db.base import Base, utcnow_naive


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    continuation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Metadata projection columns added by 0011_agent_run_metadata.
    expected_tool_call_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_a2a_invocation_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    iteration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deadline_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    started_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (
        Index("ix_agent_runs_status_created", "status", "created_at"),
        Index("ix_agent_runs_conversation_status", "conversation_id", "status", "created_at"),
        Index("ix_agent_runs_deadline", "deadline_at"),
    )
