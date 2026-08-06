"""SessionJob — writes to ``chat_sessions`` + ``chat_messages`` tables.

Each Job owns its ORM class definition (per the new_bus convention).
The ``__tablename__`` is shared with the Book's ORM class so both
write/read paths operate on the same physical table.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive

logger = logging.getLogger("magi.new_bus.jobs.session")


# -- Job-side ORM (own class, same __tablename__ as Book) ----------------


class _JChatSessionRow(JobBase):
    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    delivery_address: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active_tail_count: Mapped[int] = mapped_column(
        Integer, default=20, nullable=False
    )
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)


class _JChatMessageRow(JobBase):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_chat_messages_session_archived", "session_id", "archived", "id"),
        UniqueConstraint(
            "session_id", "message_id", name="uq_chat_messages_session_msg"
        ),
    )


# -- Job classes (sync writes) ------------------------------------------


class SessionJob(BaseJob):
    """Write side of the chat session domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def create(self, *, session_id: str, delivery_address: str, uid: int,
               channel: str, title: str | None = None,
               created_at: str = "", updated_at: str = "") -> str:
        """Insert a new chat session; return its session_id."""
        with self._factory.session() as s:
            row = _JChatSessionRow(
                session_id=session_id,
                delivery_address=delivery_address,
                uid=uid,
                channel=channel,
                title=title,
                created_at=created_at,
                updated_at=updated_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.session_id

    def touch(self, *, session_id: str, updated_at: str) -> None:
        """Update ``updated_at`` to mark the session as recently used."""
        with self._factory.session() as s:
            row = s.scalar(
                select(_JChatSessionRow)
                .where(_JChatSessionRow.session_id == session_id)
            )
            if row is None:
                return
            row.updated_at = updated_at
            s.commit()


class MessageJob(BaseJob):
    """Write side of the chat message domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def append(
        self,
        *,
        session_id: str,
        message_id: str,
        role: str,
        text: str,
        ts: str,
        content_blocks: list[dict[str, Any]] | None = None,
        run_id: str | None = None,
        llm_attempt_id: str | None = None,
    ) -> int:
        """Insert a new message; return its id."""
        with self._factory.session() as s:
            row = _JChatMessageRow(
                session_id=session_id,
                message_id=message_id,
                role=role,
                text=text,
                ts=ts,
                content_blocks=content_blocks,
                run_id=run_id,
                llm_attempt_id=llm_attempt_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def archive(self, *, message_id: int) -> None:
        """Mark a message as archived (sets ``archived=1``)."""
        with self._factory.session() as s:
            row = s.scalar(
                select(_JChatMessageRow).where(_JChatMessageRow.id == message_id)
            )
            if row is None:
                return
            row.archived = 1
            s.commit()


__all__ = ["SessionJob", "MessageJob"]
