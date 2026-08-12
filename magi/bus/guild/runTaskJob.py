"""runTaskJobBoard — 任务触发作业板。

inter-worker / tool 统一触发接口：任何调用方
``bus.run_task_job_board.publish(RunTaskJob(task_id=...))``，
TaskWorker claim 后执行同一 ``_fire_task`` 路径。

触发语义：``RunTaskJob.manual: bool`` —— True 表示用户/工具
主动触发（API / UI / tool），False 表示 task 模块按自身规则
（cron / run_at）触发。

``conversation_id`` / ``contact_id`` **不在 job 上** —— 这些
字段由 :class:`~magi.bus.library.local.tasksBook.Task` 持有，
TaskWorker claim 后通过 :meth:`tasks_book.get` 读取。这样
任务的所有 run 都自动共享同一个会话上下文（创建时由
``conversations_book.create_task_conversation`` 分配并落到
``tasks.conversation_id``），fire 时无需调用方重传。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, JobRowMixin, JobStatus


@dataclass(frozen=True, slots=True)
class RunTaskJob(BaseJob):
    """一次任务触发请求 — 任何调用方都通过同一个 board 触达 TaskWorker。

    所有触发来源（cron / run_at / API / UI / tool）共用一份
    :class:`RunTaskJob` + :class:`RunTaskResult`，由 :class:`TaskWorker`
    claim 后走统一的 ``_fire_task`` 路径。这样无论谁触发，任务
    在 worker 侧的执行路径完全一致，观测和重试预算也统一计数。

    ``manual`` 标记触发是否由用户/工具主动发起 — 影响后续
    ``plans`` 的写入决策（如 manual run 跳过 since-recent 判定）。
    与 :class:`~magi.bus.library.local.tasksBook.TaskRun.manual`
    同构。

    只携带 ``task_id`` —— 会话/联系人上下文由 worker 从
    :class:`~magi.bus.library.local.tasksBook.Task` 读取，
    确保任务创建时分配的 conversation 在所有 run 间共享。
    """

    task_id: str  # 目标 Task 的 ID（对应 tasks.id）
    manual: bool = True  # True=用户/工具主动；False=task 模块按规则（cron/run_at）


@dataclass(frozen=True, slots=True)
class RunTaskResult(BaseJobResult):
    """:class:`RunTaskJob` 的处理回执 — TaskWorker 跑完 ``_fire_task`` 后写入。

    :attr:`JobStatus.FAILED` 通常意味着 Task 找不到 / 已被禁用 /
    任务缺少 ``conversation_id``（创建契约被破坏）/
    :class:`PlanBook` 写入失败 — 这些都写在 ``error`` 里供
    上层排查。``attempts`` 由 :class:`BaseJobBoard` 回写
    ，和 ORM 的 ``attempts`` 列同步。
    """

    error: str | None = None  # 失败时的错误描述


class _RunTaskJobRow(JobRowMixin, Base):
    __tablename__ = "run_task_jobs"
    __table_args__ = {"extend_existing": True}

    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 是否用户/工具主动触发。``manual=True`` 跳过 since-recent
    #: 判定；``False`` 表示 cron / run_at 系统自触发。与
    #: :class:`~magi.bus.library.local.tasksBook.TaskRun.manual`
    #: 同构。
    manual: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    result: Mapped[dict | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
                status=JobStatus.PENDING,
                task_id=job.task_id,
                manual=int(job.manual),
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
                job_id=row.job_id,
                attempts=row.attempts,
            )
