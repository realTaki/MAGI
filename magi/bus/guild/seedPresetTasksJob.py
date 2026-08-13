"""seedPresetTaskJobBoard — 单条预设任务播种作业。

API / Tool 在联系人变为 assigned 时 publish **每个 preset 一条**
job，ProactiveWorker 异步 claim 并执行。一次 job 只插一个 Task
—— 出问题（某 preset 找不到 / 字段错）能在日志里精确定位到
``contact_id`` + ``preset_key`` 对应的那一行，而不是一锅炖
然后看到 ``inserted=3, skipped=2`` 这种需要二次回溯的计数。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import utcnow_naive
from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedPresetTaskJob(BaseJob):
    """一条预设任务播种 job — 插一个 Task。

    ``contact_id`` 是目标联系人；``preset_key`` 是
    :func:`magi.bus.library.local.promptBook.PromptBook.task_presets`
    返回字典的 key（用于 worker 端定位 YAML preset）。每次 publish
    只对应一个 Task 行—— caller 在循环里发多个 job。
    """

    contact_id: int = 0  # 目标联系人 ID
    preset_key: str = ""  # preset 在 prompt_book.task_presets() 字典中的 key


@dataclass(frozen=True, slots=True)
class SeedPresetTaskResult(BaseJobResult):
    """单条播种结果。

    ``status=JobStatus.COMPLETED`` 表示该 preset 已被插成 Task；
    ``status=JobStatus.FAILED`` 时基类的 ``error`` 字段写失败
    原因（preset 找不到 / cron 表达式非法 / Task 重复等）。无
    ``inserted`` / ``skipped`` 计数——单条粒度下计数字段只会永远
    是 0 或 1，没意义。
    """


class _SeedPresetTaskJobRow(BaseJobRowMixin):
    __tablename__ = "seed_preset_tasks_jobs"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    preset_key: Mapped[str] = mapped_column(String(128), nullable=False)

    # ``error`` column inherits ``String(1024)`` from
    # :class:`~magi.bus.guild.base.BaseJobRowMixin`. This row used
    # to declare ``Text`` here — the size was historically
    # over-sized because every preset's failure description was
    # stuffed onto one cell. With the result side
    # (:class:`~magi.bus.guild.seedPresetTasksJob.SeedPresetTaskResult`)
    # inheriting ``error`` from
    # :class:`~magi.bus.guild.base.BaseJobResult`, 1024 is plenty.


class seedPresetTaskJobBoard(
    BaseJobBoard[_SeedPresetTaskJobRow, SeedPresetTaskJob, SeedPresetTaskResult]
):
    job_model = _SeedPresetTaskJobRow
    job_cls = SeedPresetTaskJob
    result_cls = SeedPresetTaskResult


__all__ = [
    "SeedPresetTaskJob",
    "SeedPresetTaskResult",
    "seedPresetTaskJobBoard",
]
