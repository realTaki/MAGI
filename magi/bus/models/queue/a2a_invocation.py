"""ORM table: a2a_invocations (peer MAGI call lifecycle)."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.db.base import Base, utcnow_naive


class A2AInvocation(Base):
    __tablename__ = "a2a_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invocation_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    request_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    reply_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expect_reply: Mapped[bool] = mapped_column(nullable=False, default=False)
    deadline_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    request: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    completed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_a2a_invocations_run_created", "run_id", "created_at"),)
