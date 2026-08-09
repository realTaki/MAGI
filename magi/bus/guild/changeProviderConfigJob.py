"""changeProviderConfigJobBoard — provider 配置变更作业。

当 WebUI 修改 provider / API key / model 后，api 侧 publish 到本
board；:class:`ProvidersWorker` 是唯一的 consumer，claim 后重建
缓存的 SDK client 并 submit :class:`ChangeProviderConfigResult`。

``publish()`` 自己在写入 job 行前先把配置落 ``settings_book``，
调用方不需要记住这一步。

设计要点
========

- **本 board 专门服务 provider 配置变更**：只有 ``ProvidersWorker``
  一个 claimer，claim 后重建缓存的 SDK client 并 submit 结果。

- **self-contained write**：``publish()`` 同时完成"落 settings_book"
  + "创建 job 行"两步。调用方只需要构造一次
  :class:`ChangeProviderConfigJob`，不需要自己调 ``settings_book.set``。

- **命名**：job board 以动词打头（``changeProviderConfig`` → "apply
  this config change"）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, new_job_id

if TYPE_CHECKING:
    from magi.bus.library.local.settingBook import SettingBook

logger = logging.getLogger("magi.bus.guild.changeProviderConfig")


# ── settings keys ─────────────────────────────────────────────────────────

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"


# ── public dataclasses ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChangeProviderConfigJob:
    """一次 provider 配置变更。

    ``publish()`` 会自动把 ``provider`` / ``api_key`` / ``model``
    写入 ``settings_book``，调用方只管构造。
    """

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ChangeProviderConfigResult:
    job_id: str
    success: bool
    error: str | None = None


# ── internal ORM ───────────────────────────────────────────────────────────


class _ChangeProviderConfigRow(Base):
    __tablename__ = "change_provider_config_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # Field-level columns the worker reads to decide between a
    # full SDK rebuild (``provider`` / ``api_key`` set) and a
    # in-place ``provider.model`` swap (only ``model`` set).
    # All three are nullable so an incomplete change (``None``)
    # is preserved end-to-end.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 保留 payload 字段以兼容已有数据；新 publish 不再写入
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


# ── Board ──────────────────────────────────────────────────────────────────


class changeProviderConfigJobBoard(
    BaseJobBoard[_ChangeProviderConfigRow, ChangeProviderConfigJob, ChangeProviderConfigResult]
):
    """Provider 配置变更作业板。

    ``publish()`` 写入两步：先落 ``settings_book``，再建 job 行。
    """

    job_model = _ChangeProviderConfigRow
    job_cls = ChangeProviderConfigJob
    result_cls = ChangeProviderConfigResult

    def __init__(self, factory, *, settings_book: SettingBook | None = None):
        super().__init__(factory)
        self._settings_book = settings_book

    def publish(self, job: ChangeProviderConfigJob) -> str:
        # 1. 把配置写入 settings_book（调用方不需要记住这步）。
        if self._settings_book is not None:
            self._write_to_settings(job)

        # 2. 创建 job 行。Field-level columns let the worker decide
        #    between a full SDK rebuild (provider / api_key set) and
        #    an in-place model swap (only model set); the legacy
        #    ``payload`` is kept for audit only.
        job_id = job.job_id or new_job_id()
        with self._session() as s:
            row = _ChangeProviderConfigRow(
                job_id=job_id,
                status="pending",
                provider=job.provider,
                api_key=job.api_key,
                model=job.model,
                payload={
                    "provider": job.provider,
                    "api_key_last4": (job.api_key or "")[-4:] or None,
                    "model": job.model,
                },
            )
            s.add(row)
            s.flush()
            s.commit()
        return job_id

    def _write_to_settings(self, job: ChangeProviderConfigJob) -> None:
        """Upsert provider config into ``settings_book``."""
        # ``publish()`` already guards ``self._settings_book`` being
        # non-None; the explicit early-return here narrows the type
        # for the ``.set`` calls below (Pylance otherwise sees
        # ``sb`` as ``SettingBook | None`` and flags unknown attr).
        sb = self._settings_book
        if sb is None:
            return
        if job.provider is not None:
            sb.set(key=PROVIDER_NAME_KEY, value=job.provider)
        if job.api_key is not None:
            sb.set(key=PROVIDER_API_KEY_KEY, value=job.api_key)
        if job.model is not None:
            sb.set(key=PROVIDER_MODEL_KEY, value=job.model)
        logger.info(
            "changeProviderConfig: wrote provider=%r model=%r to settings_book",
            job.provider, job.model,
        )
