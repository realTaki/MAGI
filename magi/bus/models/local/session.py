"""SQLAlchemy storage models for the BUS-owned chat-session domain."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.db.base import Base


class ChatSession(Base):
    """Session header; callers access it only through BUS services."""

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    delivery_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active_tail_count: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", viewonly=True,
        order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    """One persisted transcript message, active or archived."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True,
    )
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_blocks: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_chat_messages_session_archived", "session_id", "archived", "id"),
        UniqueConstraint("session_id", "message_id", name="uq_chat_messages_session_msg"),
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages", viewonly=True)
