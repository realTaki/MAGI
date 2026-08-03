"""ORM table: tool_jobs (durable tool execution job, with lease/retry budget)."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus._persistence.base import Base, utcnow_naive


class ToolJob(Base):
    __tablename__ = "tool_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Snapshot fields bind execution to exactly the catalog definition the
    # actor offered to the provider.
    tool_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    catalog_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    available_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (Index("ix_tool_jobs_claim", "status", "available_at", "id"),)
