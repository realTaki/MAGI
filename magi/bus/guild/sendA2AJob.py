"""sendA2AJobBoard — peer-MAGI call lifecycle.

Backed by the ``a2a_jobs`` table.  Natural key is ``job_id`` (same
shape as :class:`runTaskJobBoard` and :class:`chatJobBoard`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, new_job_id

# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class SendA2AJob:
    """Publisher input — one row per peer-MAGI call."""

    job_id: str = ""
    target: str = ""
    tool_call_id: str | None = None
    request_event_id: str | None = None
    reply_to: str | None = None
    expect_reply: bool = False
    deadline_at: datetime | None = None
    idempotency_key: str | None = None
    request: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SendA2AResult:
    """Worker output — terminal state of one peer-MAGI call."""

    job_id: str = ""
    success: bool = False
    status: str = "failed"
    response: dict[str, Any] | None = None
    error: str = ""


# -- internal ORM --------------------------------------------------------


class _A2AJobRow(Base):
    __tablename__ = "a2a_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    request_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    reply_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expect_reply: Mapped[bool] = mapped_column(nullable=False, default=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class sendA2AJobBoard(
    BaseJobBoard[_A2AJobRow, SendA2AJob, SendA2AResult]
):
    """Queue (write + claim + submit_result) for peer-MAGI calls."""

    job_model = _A2AJobRow
    job_cls = SendA2AJob
    result_cls = SendA2AResult
    natural_key_attr = "job_id"

    def _insert_pending(self, session, job: SendA2AJob, **kwargs) -> _A2AJobRow:
        job_id = job.job_id or new_job_id()
        row = _A2AJobRow(
            job_id=job_id,
            target=job.target,
            tool_call_id=job.tool_call_id,
            request_event_id=job.request_event_id,
            reply_to=job.reply_to,
            expect_reply=job.expect_reply,
            deadline_at=job.deadline_at,
            idempotency_key=job.idempotency_key,
            request=job.request or {},
            status="requested",
        )
        session.add(row)
        session.flush()
        return row


__all__ = [
    "SendA2AJob",
    "SendA2AResult",
    "sendA2AJobBoard",
    "_A2AJobRow",
]