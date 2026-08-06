"""ConfigJob — 配置变更作业。

一张表 ``config_jobs``，包含：
- ConfigJob:       ORM 模型
- ConfigJobResult:  执行结果
- publish_config_job: 发布
- claim_config_job:   认领
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive

DEFAULT_LEASE_SECONDS = 60


# -- ORM -------------------------------------------------------------------

class ConfigJob(Base):
    __tablename__ = "config_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    config_key: Mapped[str] = mapped_column(String(128), nullable=False)
    config_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Result ----------------------------------------------------------------

@dataclass
class ConfigJobResult:
    job_id: str
    success: bool
    message: str = ""
    data: dict | None = None


# -- publish ---------------------------------------------------------------

def publish_config_job(
    session: Session,
    *,
    config_key: str,
    config_value: dict | None = None,
) -> ConfigJob:
    """发布一个配置变更作业。"""
    now = utcnow_naive()
    job = ConfigJob(
        job_id=uuid.uuid4().hex,
        status="pending",
        config_key=config_key,
        config_value=config_value,
    )
    session.add(job)
    session.flush()
    return job


# -- claim -----------------------------------------------------------------

def claim_config_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> ConfigJob | None:
    """认领一个待处理的 ConfigJob（带租约）。"""
    now = utcnow_naive()
    lease_until = now + timedelta(seconds=lease_seconds)

    job = session.scalar(
        select(ConfigJob)
        .where(ConfigJob.status == "pending")
        .order_by(ConfigJob.created_at, ConfigJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None

    job.status = "processing"
    job.leased_by = worker_id
    job.leased_until = lease_until
    job.attempts += 1
    return job
