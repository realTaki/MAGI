"""seedPresetTasksJobBoard — 预设任务播种作业。

API / Tool 在联系人变为 assigned 时 publish，ProactiveWorker 异步 claim 并执行。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base
from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin, JobStatus


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedPresetTasksJob(BaseJob):
    """一个预设任务播种 job。``contact_id`` 是目标联系人。"""

    contact_id: int = 0  # 目标联系人 ID


@dataclass(frozen=True, slots=True)
class SeedPresetTasksResult(BaseJobResult):
    """播种完成结果。

    ``inserted`` 是实际插入的 Task 行数；``skipped`` 是跳过
    的 preset 数（含仅跳过和 planner 标记跳过两类）。
    """

    inserted: int = 0  # 实际插入的 Task 行数
    skipped: int = 0  # 跳过的 preset 数
    error: str | None = None  # 失败时的错误描述

    # lease / retry bookkeeping (written back by BaseJobBoard)
    attempts: int = 0  # 已重试次数（由 BaseJobBoard 回写）


class _SeedPresetTasksJobRow(BaseJobRowMixin, Base):
    __tablename__ = "seed_preset_tasks_jobs"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # -- result-side columns ------------------------------------------
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
                status=JobStatus.PENDING,
                contact_id=job.contact_id,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id


__all__ = [
    "SeedPresetTasksJob",
    "SeedPresetTasksResult",
    "seedPresetTasksJobBoard",
]