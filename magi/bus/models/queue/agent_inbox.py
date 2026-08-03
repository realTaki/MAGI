"""ORM table: agent_inbox (one row per durable agent turn request).

Moved from ``magi/bus/models.py`` to its own module so the bus-owned
ORM is partitioned by responsibility.  All other queue tables live
alongside this one under :mod:`magi.bus.models.queue`.
"""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class AgentInbox(Base):
    __tablename__ = "agent_inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # A run receives its root input plus later steering/tool/A2A result
    # events, so this is intentionally indexed rather than unique.
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Cross-channel idempotency triple (added by 0009_idempotency_keys).
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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

    __table_args__ = (
        Index("ix_agent_inbox_claim", "status", "available_at", "id"),
        Index("ix_agent_inbox_lease", "status", "leased_until"),
        Index("ix_agent_inbox_run", "run_id", "id"),
        Index("ix_agent_inbox_conversation", "conversation_id", "id"),
    )
