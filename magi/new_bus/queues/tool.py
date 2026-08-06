"""ToolJobQueue — durable tool execution queue.

Backed by the ``tool_jobs`` table (parallel to old bus's
``magi.bus.db.models.queue.tool_job.ToolJob``).  Natural key is
``job_id``.

A second Book, ``ToolCallBook`` (in ``magi.new_bus.books.local.tool``),
manages the related ``tool_calls`` table (one row per within-run
tool call record).  Keeping them as separate tables is intentional:
``tool_jobs`` is the durable execution queue; ``tool_calls`` is the
within-run audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.queues.base import BaseJobQueue, new_job_id


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolJob:
    """Publisher input — one row per tool execution."""

    job_id: str = ""
    run_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_source: str | None = None
    catalog_revision: int | None = None
    schema_hash: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] | None = None
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class ToolJobResult:
    """Worker output — final state of one tool execution."""

    job_id: str = ""
    success: bool = False
    status: str = "failed"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    attempts: int = 0


# -- internal ORM --------------------------------------------------------


class _ToolJobRow(Base):
    __tablename__ = "tool_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    catalog_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (Index("ix_tool_jobs_run_id", "run_id"),)


# -- Queue ----------------------------------------------------------------


class ToolJobQueue(BaseJobQueue[_ToolJobRow, ToolJob, ToolJobResult]):
    job_model = _ToolJobRow
    job_cls = ToolJob
    result_cls = ToolJobResult
    natural_key_attr = "job_id"

    def _insert_pending(self, session, job: ToolJob, **kwargs) -> _ToolJobRow:
        job_id = job.job_id or new_job_id()
        row = _ToolJobRow(
            job_id=job_id,
            run_id=job.run_id,
            tool_call_id=job.tool_call_id,
            tool_name=job.tool_name,
            tool_source=job.tool_source,
            catalog_revision=job.catalog_revision,
            schema_hash=job.schema_hash,
            idempotency_key=job.idempotency_key,
            payload=job.payload or {},
            status="pending",
            max_attempts=job.max_attempts or 3,
        )
        session.add(row)
        session.flush()
        return row


__all__ = [
    "ToolJob",
    "ToolJobResult",
    "ToolJobQueue",
    "_ToolJobRow",
]
