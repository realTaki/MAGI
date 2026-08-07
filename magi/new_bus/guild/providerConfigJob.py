"""providerConfigJobBoard — provider 专属配置变更作业。

与通用 ``controlJobBoard`` 不同，这个 board 只有 ``ProvidersWorker``
会 claim。WebUI 修改 provider/api_key/model 后 publish 到此处，
provider worker 收到后重建缓存的 SDK client。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard


# -- public dataclasses ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProviderConfigJob:
    """一次 provider 配置变更。

    ``payload`` 可携带 ``{provider, model}`` 等变更详情，
    目前 provider worker 只关心"变了"这一事实本身，立即重建。
    """

    payload: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ProviderConfigResult:
    job_id: str
    success: bool
    error: str | None = None


# -- internal ORM ----------------------------------------------------------

class _ProviderConfigRow(Base):
    __tablename__ = "provider_config_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Board -----------------------------------------------------------------

class providerConfigJobBoard(
    BaseJobBoard[_ProviderConfigRow, ProviderConfigJob, ProviderConfigResult]
):
    job_model = _ProviderConfigRow
    job_cls = ProviderConfigJob
    result_cls = ProviderConfigResult

    def publish(self, job: ProviderConfigJob) -> str:
        with self._factory.session() as s:
            row = _ProviderConfigRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                payload=job.payload,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
