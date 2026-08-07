"""changeProviderConfigJobBoard — provider 配置变更通知。

当 WebUI 修改 provider / API key / model 后，api 侧 publish 到本
board；:class:`ProvidersWorker` 是唯一的 consumer，claim 后重建
缓存的 SDK client 并 submit :class:`ChangeProviderConfigResult`。

设计要点
========

- **与 ``controlJobBoard`` 区分**：``controlJob`` 是 generic 的
  运行时信号 channel，多个 worker 都可能 claim；本 board 专门
  服务 provider 配置变更，只有一个 claimer（provider worker）。
  将来其他模块需要类似的"配置变更触发重建"语义时，各开自己的
  board，不共用 controlJob。

- **payload 只用于审计 / 调试**：worker 重建时不需要解析 payload，
  因为它直接重新读 ``bus.magic.provider_configuration()`` 拿到最新
  状态。payload 字段保留是为 audit 行能记下"这次是什么变了"。

- **fire-and-forget friendly**：调用方 publish 后不需要等 result；
  worker claim → rebuild → submit 即可，最坏情况是 result 行一直
  pending，audit 能查到。

- **命名**：job board 必须以动词打头（``runAgent`` / ``sendA2A`` /
  ``chat`` / ``callLLM`` / ...），所以这里是 ``changeProviderConfig``
  —— "apply this config change"。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard, new_job_id


# -- public dataclasses ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class ChangeProviderConfigJob:
    """一次 provider 配置变更。

    ``payload`` 可携带 ``{provider, model}`` 等变更详情，
    目前 provider worker 只关心"变了"这一事实本身，立即重建。
    """

    payload: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ChangeProviderConfigResult:
    job_id: str
    success: bool
    error: str | None = None


# -- internal ORM ----------------------------------------------------------

class _ChangeProviderConfigRow(Base):
    __tablename__ = "change_provider_config_jobs"
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

class changeProviderConfigJobBoard(
    BaseJobBoard[_ChangeProviderConfigRow, ChangeProviderConfigJob, ChangeProviderConfigResult]
):
    job_model = _ChangeProviderConfigRow
    job_cls = ChangeProviderConfigJob
    result_cls = ChangeProviderConfigResult

    def publish(self, job: ChangeProviderConfigJob) -> str:
        with self._factory.session() as s:
            row = _ChangeProviderConfigRow(
                job_id=job.job_id or new_job_id(),
                status="pending",
                payload=job.payload,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
