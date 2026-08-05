"""ORM table: llm_attempts (per-inference lifecycle record)."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class LLMAttempt(Base):
    __tablename__ = "llm_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    inbox_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_stream_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="started")
    request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    completed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    # Lease tracking — Phase B (provider-worker queue). NULL when the
    # row is in its free state; populated while a :class:`ProvidersWorker`
    # is in flight. ``recover_expired_provider_leases`` reclaims rows
    # whose lease has passed without a terminal write.
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_llm_attempts_run_started", "run_id", "started_at"),)
