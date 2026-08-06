"""LLMJobQueue — provider-worker LLM inference queue.

Backed by the ``llm_attempts`` table (parallel to old bus's
``magi.bus.db.models.queue.llm_attempt.LLMAttempt``).  Natural key is
``attempt_id``.

Does NOT support ``inline=True`` — the LLM inference is a real worker
job, not a side-effect the publisher can do.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.queues.base import BaseJobQueue, new_job_id


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMJob:
    """Snapshot of the LLM inference request as the publisher sent it."""

    attempt_id: str = ""
    run_id: str = ""
    inbox_event_id: str | None = None
    provider: str | None = None
    model: str | None = None
    phase: str = "started"
    request: dict[str, Any] | None = None
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LLMJobResult:
    """Final state of an LLM attempt (success or failure)."""

    attempt_id: str = ""
    success: bool = False
    status: str = "failed"
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    completed_at: datetime | None = None
    error_code: str = ""


# -- internal ORM --------------------------------------------------------


class _LLMJobRow(Base):
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
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_llm_attempts_run_started", "run_id", "started_at"),)


# -- Queue ----------------------------------------------------------------


class LLMJobQueue(BaseJobQueue[_LLMJobRow, LLMJob, LLMJobResult]):
    job_model = _LLMJobRow
    job_cls = LLMJob
    result_cls = LLMJobResult
    natural_key_attr = "attempt_id"

    def _insert_pending(self, session, job: LLMJob, **kwargs) -> _LLMJobRow:
        attempt_id = job.attempt_id or new_job_id()
        row = _LLMJobRow(
            attempt_id=attempt_id,
            run_id=job.run_id,
            inbox_event_id=job.inbox_event_id,
            provider=job.provider,
            model=job.model,
            phase=job.phase or "started",
            status="pending",
            request=job.request,
        )
        session.add(row)
        session.flush()
        return row

    def get(self, *, attempt_id: str) -> LLMJob | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_LLMJobRow).where(_LLMJobRow.attempt_id == attempt_id)
            )
            return self._row_to_job(row) if row else None


__all__ = [
    "LLMJob",
    "LLMJobResult",
    "LLMJobQueue",
    "_LLMJobRow",
]
