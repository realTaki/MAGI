"""SQLAlchemy model for BUS-owned local long-term memory."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive

KIND_IMPORTANT = "important"
KIND_ONGOING = "ongoing"
ALL_KINDS = frozenset({KIND_IMPORTANT, KIND_ONGOING})
SOURCE_MANUAL = "manual"
SOURCE_EVA = "eva"
SOURCE_SYSTEM = "system"


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_EVA)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False,
    )

    __table_args__ = (Index("ix_memory_entries_owner_importance", "uid", "completed_at", "importance"),)
