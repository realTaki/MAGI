"""LLMJob — LLM 推理作业。

一张表 ``llm_jobs``，包含：
- LLMJob:       ORM 模型
- LLMJobResult:  执行结果
- publish_llm_job: 发布
- claim_llm_job:   认领
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, Float, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive

DEFAULT_LEASE_SECONDS = 60


# -- ORM -------------------------------------------------------------------

class LLMJob(Base):
    __tablename__ = "llm_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # 请求
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    messages: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 租约
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 结果
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Result ----------------------------------------------------------------

@dataclass
class LLMJobResult:
    job_id: str
    success: bool
    response: dict | None = None
    finish_reason: str | None = None
    token_usage: dict | None = None
    error: str | None = None


# -- publish ---------------------------------------------------------------

def publish_llm_job(
    session: Session,
    *,
    model: str,
    messages: list[dict],
    parameters: dict | None = None,
) -> LLMJob:
    """发布一个 LLM 推理作业。"""
    job = LLMJob(
        job_id=uuid.uuid4().hex,
        status="pending",
        model=model,
        messages=messages,
        parameters=parameters,
    )
    session.add(job)
    session.flush()
    return job


# -- claim -----------------------------------------------------------------

def claim_llm_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> LLMJob | None:
    """认领一个待处理的 LLMJob（带租约）。"""
    now = utcnow_naive()
    lease_until = now + timedelta(seconds=lease_seconds)

    job = session.scalar(
        select(LLMJob)
        .where(LLMJob.status == "pending")
        .order_by(LLMJob.created_at, LLMJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None

    job.status = "processing"
    job.leased_by = worker_id
    job.leased_until = lease_until
    job.attempts += 1
    job.started_at = utcnow_naive()
    return job
