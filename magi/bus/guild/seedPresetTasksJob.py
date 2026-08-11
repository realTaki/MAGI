"""seedPresetTasksJobBoard — 预设任务播种作业。

API / Tool 在联系人变为 assigned 时 publish，ProactiveWorker 异步 claim 并执行。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard


@dataclass(frozen=True, slots=True)
class SeedPresetTasksJob:
    """一个预设任务播种 job。

    ``contact_id`` 是目标联系人；``trigger`` 标记触发来源
    (``"contact_created"`` / ``"contact_promoted"``)。
    """

    job_id: str = ""  # 发布时自动生成的 job_id
    contact_id: int = 0  # 目标联系人 ID
    trigger: str = ""  # 触发来源（contact_created/contact_promoted）
    status: str = "pending"  # job 当前状态
    attempts: int = 0  # 已重试次数


@dataclass(frozen=True, slots=True)
class SeedPresetTasksResult:
    """播种完成结果。

    ``inserted`` 是实际插入的 Task 行数；``skipped`` 是跳过
    的 preset 数（含仅跳过和 planner 标记跳过两类）。
    """

    job_id: str  # 对应 SeedPresetTasksJob 的 job_id
    success: bool  # 播种是否成功
    inserted: int = 0  # 实际插入的 Task 行数
    skipped: int = 0  # 跳过的 preset 数
    error: str | None = None  # 失败时的错误描述

    # lease / retry bookkeeping (written back by BaseJobBoard)
    attempts: int = 0  # 已重试次数（由 BaseJobBoard 回写）


class _SeedPresetTasksJobRow(Base):
    __tablename__ = "seed_preset_tasks_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)

    # -- result-side columns ------------------------------------------
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- lease / retry bookkeeping ------------------------------------
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # -- timestamps ---------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class seedPresetTasksJobBoard(
    BaseJobBoard[_SeedPresetTasksJobRow, SeedPresetTasksJob, SeedPresetTasksResult]
):
    job_model = _SeedPresetTasksJobRow
    job_cls = SeedPresetTasksJob
    result_cls = SeedPresetTasksResult

    def publish(self, job: SeedPresetTasksJob) -> str:
        with self._session() as s:
            row = _SeedPresetTasksJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                contact_id=job.contact_id,
                trigger=job.trigger,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
