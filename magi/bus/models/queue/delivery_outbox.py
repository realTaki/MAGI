"""ORM table: delivery_outbox (committed channel delivery effects awaiting I/O)."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus._persistence.base import Base, utcnow_naive


class DeliveryOutbox(Base):
    __tablename__ = "delivery_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    available_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (Index("ix_delivery_outbox_claim", "status", "available_at", "id"),)
