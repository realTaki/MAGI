"""callLLMJobBoard — LLM 推理作业。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard


# -- public dataclasses ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class CallLLMJob:
    model: str
    messages: list[dict]
    parameters: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class CallLLMResult:
    job_id: str
    success: bool
    response: dict | None = None
    finish_reason: str | None = None
    token_usage: dict | None = None
    error: str | None = None


# -- internal ORM ----------------------------------------------------------

class _LLMJobRow(Base):
    __tablename__ = "llm_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    messages: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Queue -----------------------------------------------------------------

class callLLMJobBoard(BaseJobBoard[_LLMJobRow, CallLLMJob, CallLLMResult]):
    job_model = _LLMJobRow
    job_cls = CallLLMJob
    result_cls = CallLLMResult

    def publish(self, job: CallLLMJob) -> str:
        with self._factory.session() as s:
            row = _LLMJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                model=job.model,
                messages=job.messages,
                parameters=job.parameters,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
