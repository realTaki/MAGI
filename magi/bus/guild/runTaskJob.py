"""runTaskJobBoard — 任务触发作业板。

inter-worker / tool 统一触发接口：任何调用方
``bus.run_task_job_board.publish(RunTaskJob(task_id=...))``，
TaskWorker claim 后执行同一 ``_fire_task`` 路径。

触发语义：``RunTaskJob.manual: bool`` —— True 表示用户/工具
主动触发（API / UI / tool），False 表示 task 模块按自身规则
（cron / run_at）触发。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard


@dataclass(frozen=True, slots=True)
class RunTaskJob:
    """一次任务触发请求 — 任何调用方都通过同一个 board 触达 TaskWorker。

    所有触发来源（cron / run_at / API / UI / tool）共用一份
    :class:`RunTaskJob` + :class:`RunTaskResult`，由 :class:`TaskWorker`
    claim 后走统一的 ``_fire_task`` 路径。这样无论谁触发，任务
    在 worker 侧的执行路径完全一致，观测和重试预算也统一计数。

    ``manual`` 标记触发是否由用户/工具主动发起 — 影响后续
    ``plans`` 的写入决策（如 manual run 跳过 since-recent 判定）。
    与 :class:`~magi.bus.library.local.tasksBook.TaskRun.manual`
    同构。
    """

    task_id: str  # 目标 Task 的 ID（对应 tasks.id）
    manual: bool = True  # True=用户/工具主动；False=task 模块按规则（cron/run_at）
    conversation_id: str | None = None  # 可选的会话上下文（仅供 worker 审计用）
    contact_id: int | None = None  # 可选的联系人上下文（仅供 worker 审计用）
    job_id: str = ""  # 发布时自动生成的 job_id
    # Populated by ``BaseJobBoard._map_row`` on claim — not stored on
    # the row (the column exists as a counter only). Exposed here so
    # callers can observe lease-recovery behaviour (see
    # ``test_lease_expiry_reclaims_abandoned_job``).
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class RunTaskResult:
    """:class:`RunTaskJob` 的处理回执 — TaskWorker 跑完 ``_fire_task`` 后写入。

    ``success=False`` 通常意味着 Task 找不到 / 已被禁用 /
    ：class:`PlanBook` 写入失败 — 这些都写在 ``error`` 里供
    上层排查。``attempts`` 由 :class:`BaseJobBoard` 回写
    ，和 ORM 的 ``attempts`` 列同步。
    """

    job_id: str  # 对应 RunTaskJob 的 job_id
    success: bool  # Task 是否成功触发（plan 已落库）
    error: str | None = None  # 失败时的错误描述


class _RunTaskJobRow(Base):
    __tablename__ = "run_task_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 是否用户/工具主动触发。``manual=True`` 跳过 since-recent
    #: 判定；``False`` 表示 cron / run_at 系统自触发。与
    #: :class:`~magi.bus.library.local.tasksBook.TaskRun.manual`
    #: 同构。
    manual: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )


class runTaskJobBoard(BaseJobBoard[_RunTaskJobRow, RunTaskJob, RunTaskResult]):
    job_model = _RunTaskJobRow
    job_cls = RunTaskJob
    result_cls = RunTaskResult
    natural_key_attr = "job_id"

    def publish(self, job: RunTaskJob) -> str:
        with self._session() as s:
            row = _RunTaskJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                task_id=job.task_id,
                manual=int(job.manual),
                conversation_id=job.conversation_id,
                contact_id=job.contact_id,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id

    def claim(self) -> RunTaskJob | None:
        with self._session() as s:
            row = self._claim(s)
            s.commit()
            if row is None:
                return None
            return RunTaskJob(
                task_id=row.task_id,
                manual=bool(row.manual),
                conversation_id=row.conversation_id,
                contact_id=row.contact_id,
                job_id=row.job_id,
                attempts=row.attempts,
            )
