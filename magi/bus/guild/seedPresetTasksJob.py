"""seedPresetTasksJobBoard — 预设任务播种作业。

API / Tool 在联系人变为 assigned 时 publish，ProactiveWorker 异步 claim 并执行。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard


# -- public enum ---------------------------------------------------------


class SeedPresetTrigger(StrEnum):
    """触发来源 discriminator stored on :class:`SeedPresetTasksJob.trigger`.

    * :attr:`CONTACT_CREATED` — 新联系人以 ``assigned`` 身份落地时
      publish，由 API 创建路径触发。
    * :attr:`CONTACT_PROMOTED` — 已存在的联系人从 ``guest`` /
      ``admin`` 角色升级为 ``assigned`` 时 publish，由角色变更路径触发。

    两条触发路径的预设选择 / 写入路径完全一致；``trigger`` 只
    是审计 / 排障用的来源标记，未来可能按它走不同的 preset 集
    或埋点策略。

    ``StrEnum`` 而非裸字符串常量——typo 在 publish 时立即被
    :meth:`SeedPresetTasksJob.__post_init__` 抛回，不会悄悄写
    入数据库。成员仍为 ``str`` 子类
    （``SeedPresetTrigger.CONTACT_CREATED == "contact_created"``），
    所以 ORM 列、``==`` 与历史行的字符串值依然兼容。Mirrors
    :class:`~magi.bus.guild.mcpServerChangedJob.MCPKind` /
    :class:`~magi.bus.library.local.contactBook.Role`。
    See ``docs/insights/ENUM_MIGRATION_INVENTORY.md`` §1.6.
    """

    CONTACT_CREATED = "contact_created"
    CONTACT_PROMOTED = "contact_promoted"


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedPresetTasksJob:
    """一个预设任务播种 job。

    ``contact_id`` 是目标联系人；``trigger`` 标记触发来源
    （取自 :class:`SeedPresetTrigger`）。
    """

    trigger: SeedPresetTrigger  # 触发来源（contact_created/contact_promoted）
    job_id: str = ""  # 发布时自动生成的 job_id
    contact_id: int = 0  # 目标联系人 ID
    status: str = "pending"  # job 当前状态
    attempts: int = 0  # 已重试次数

    def __post_init__(self) -> None:
        if self.trigger not in SeedPresetTrigger:
            raise ValueError(
                f"invalid trigger {self.trigger!r}; expected one of "
                f"{sorted(t.value for t in SeedPresetTrigger)!r}"
            )


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
    #: 触发来源 discriminator。保持 ``String(32)`` 不切到
    #: ``SAEnum``——:class:`SeedPresetTrigger` 是 ``str`` 子类，
    #: 列里存的就是 ``.value``，与历史行零迁移成本。
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


__all__ = [
    "SeedPresetTrigger",
    "SeedPresetTasksJob",
    "SeedPresetTasksResult",
    "seedPresetTasksJobBoard",
]