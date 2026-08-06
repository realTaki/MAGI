"""ConfigJob — 配置变更作业。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseJobQueue


# -- public dataclasses ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConfigJob:
    config_key: str
    config_value: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ConfigJobResult:
    job_id: str
    success: bool
    error: str = ""
    result: dict | None = None


# -- internal ORM ----------------------------------------------------------

class _ConfigJobRow(Base):
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


# -- Queue -----------------------------------------------------------------

class ConfigJobQueue(BaseJobQueue[_ConfigJobRow, ConfigJob, ConfigJobResult]):
    job_model = _ConfigJobRow
    job_cls = ConfigJob
    result_cls = ConfigJobResult

    def publish(self, job: ConfigJob) -> str:
        with self._factory.session() as s:
            row = _ConfigJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                config_key=job.config_key,
                config_value=job.config_value,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
