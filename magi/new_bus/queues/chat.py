"""ChatJobQueue — chat message queue (parallel to new_bus template).

Backed by a (hypothetical) ``chat_jobs`` table.  The old bus doesn't
have this table; new_bus defines it for the new chat-job pattern.

This Queue is included for completeness with the new_bus template
(``magi/new_bus/jobs/ChatJob.py``).  In practice the agent-run flow
goes through :class:`AgentRunQueue` (``agent_inbox`` table); this
Queue is reserved for a future dedicated chat-message publishing
flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.queues.base import BaseJobQueue, new_job_id


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatJob:
    """Publisher input — one row per chat message."""

    text: str = ""
    conversation_id: str = ""
    channel: str = ""
    metadata: dict[str, Any] | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ChatJobResult:
    """Worker output — terminal state of one chat message."""

    job_id: str = ""
    success: bool = False
    status: str = "failed"
    reply: str | None = None
    error: str | None = None


# -- internal ORM --------------------------------------------------------


class _ChatJobRow(Base):
    __tablename__ = "chat_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (Index("ix_chat_jobs_conversation", "conversation_id"),)


# -- Queue ----------------------------------------------------------------


class ChatJobQueue(BaseJobQueue[_ChatJobRow, ChatJob, ChatJobResult]):
    job_model = _ChatJobRow
    job_cls = ChatJob
    result_cls = ChatJobResult
    natural_key_attr = "job_id"

    def _insert_pending(self, session, job: ChatJob, **kwargs) -> _ChatJobRow:
        job_id = job.job_id or new_job_id()
        row = _ChatJobRow(
            job_id=job_id,
            status="pending",
            text=job.text,
            conversation_id=job.conversation_id,
            channel=job.channel,
            metadata_=job.metadata,
        )
        session.add(row)
        session.flush()
        return row


__all__ = ["ChatJob", "ChatJobResult", "ChatJobQueue", "_ChatJobRow"]
