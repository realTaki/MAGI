"""runToolJob — 工具执行作业。

worker claim → 执行工具 → submit_result
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobQueue


@dataclass(frozen=True, slots=True)
class RunToolJob:
    tool_name: str
    payload: dict
    run_id: str = ""
    tool_call_id: str = ""
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class RunToolResult:
    job_id: str
    success: bool
    result: dict | None = None
    error: str | None = None


class _ToolJobRow(Base):
    __tablename__ = "tool_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), default="")
    tool_call_id: Mapped[str] = mapped_column(String(128), default="")
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class runToolJob(BaseJobQueue[_ToolJobRow, RunToolJob, RunToolResult]):
    job_model = _ToolJobRow
    job_cls = RunToolJob
    result_cls = RunToolResult

    def publish(self, job: RunToolJob) -> str:
        with self._factory.session() as s:
            row = _ToolJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                tool_name=job.tool_name,
                payload=job.payload,
                run_id=job.run_id,
                tool_call_id=job.tool_call_id,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
