"""AgentRunQueue — durable agent turn queue.

Backed by the ``agent_inbox`` + ``agent_runs`` + ``run_inputs`` tables
(parallel to old bus's
``magi.bus.db.models.queue.agent_inbox.AgentInbox`` etc.).

A publish inserts a new ``AgentInbox`` row.  A claim picks up the
oldest pending row, updates its ``status`` and lease fields, and
returns the job snapshot.  Submitting the result moves the row's
``status`` to ``completed``/``failed`` and writes the result JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.queues.base import BaseJobQueue, new_job_id


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRunJob:
    """Snapshot of a turn request (publisher input)."""

    event_id: str = ""
    run_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None
    kind: str = "chat"
    payload: dict[str, Any] | None = None
    inbox_event_id: str | None = None
    available_at: datetime | None = None
    received_seq: int = 0


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Final state of a turn."""

    event_id: str = ""
    success: bool = False
    status: str = "failed"
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None


# -- internal ORM --------------------------------------------------------


class _AgentInboxRow(Base):
    __tablename__ = "agent_inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    inbox_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    received_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    available_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Queue ----------------------------------------------------------------


class AgentRunQueue(BaseJobQueue[_AgentInboxRow, AgentRunJob, AgentRunResult]):
    job_model = _AgentInboxRow
    job_cls = AgentRunJob
    result_cls = AgentRunResult
    natural_key_attr = "event_id"

    def _insert_pending(self, session, job: AgentRunJob, **kwargs) -> _AgentInboxRow:
        event_id = job.event_id or new_job_id()
        row = _AgentInboxRow(
            event_id=event_id,
            run_id=job.run_id,
            conversation_id=job.conversation_id,
            correlation_id=job.correlation_id,
            inbox_event_id=job.inbox_event_id,
            kind=job.kind or "chat",
            payload=job.payload,
            received_seq=job.received_seq,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row


__all__ = [
    "AgentRunJob",
    "AgentRunResult",
    "AgentRunQueue",
    "_AgentInboxRow",
]
