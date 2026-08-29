"""runTaskJobBoard — 任务触发通知板（单向，fire-and-forget）。

inter-worker / tool 统一触发接口：任何调用方
``bus.run_task_job_board.publish(RunTaskJob(task_id=...))``，
TaskWorker claim 后执行同一 ``_fire_task`` 路径。

与 ``callLLMJobBoard`` / ``runToolJobBoard`` 的往返语义不同，
本 board 是**单向触发通知**：publish 后调用方不等待回执，任务
真正的执行结果走 agent → delivery 链路返回用户，不回写这里。
``submit_result`` 落终态（COMPLETED / FAILED + error）仅用于
worker 重试预算与审计排查，不是给调用方的业务回执。

触发语义：``RunTaskJob.manual: bool`` —— True 表示用户/工具
主动触发（API / UI / tool），False 表示 task 模块按自身规则
（cron / run_at）触发。

``conversation_id`` / ``contact_id`` **不在 job 上** —— 这些
字段由 :class:`~bus.firmwares.books.local.tasksBook.Task` 持有，
TaskWorker claim 后通过 :meth:`tasks_book.get` 读取。这样
任务的所有 run 都自动共享同一个会话上下文（创建时由
``conversations_book.create_task_conversation`` 分配并落到
``tasks.conversation_id``），fire 时无需调用方重传。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.job import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin


@dataclass(frozen=True, slots=True)
class RunTaskJob(BaseJob):
    """一次任务触发请求 — 任何调用方都通过同一个 board 触达 TaskWorker。

    所有触发来源（cron / run_at / API / UI / tool）共用一份
    :class:`RunTaskJob` + :class:`RunTaskResult`，由 :class:`TaskWorker`
    claim 后走统一的 ``_fire_task`` 路径。这样无论谁触发，任务
    在 worker 侧的执行路径完全一致，观测和重试预算也统一计数。

    ``manual`` 标记触发是否由用户/工具主动发起 — 影响后续
    ``plans`` 的写入决策（如 manual run 跳过 since-recent 判定）。
    与 :class:`~bus.firmwares.books.local.tasksBook.TaskRun.manual`
    同构。

    只携带 ``task_id`` —— 会话/联系人上下文由 worker 从
    :class:`~bus.firmwares.books.local.tasksBook.Task` 读取，
    确保任务创建时分配的 conversation 在所有 run 间共享。
    """

    task_id: str  # 目标 Task 的业务 ID（对应 tasks.task_id）
    manual: bool = True  # True=用户/工具主动；False=task 模块按规则（cron/run_at）


@dataclass(frozen=True, slots=True)
class RunTaskResult(BaseJobResult):
    """单向触发通知的终端回执 — 仅在 worker 处理完成/失败后落库。

    发布方（API ``run_task_now`` / tool）**不等待**此回执 ——
    :class:`runTaskJobBoard` 是 fire-and-forget 队列。此 Result
    只承载继承的 :attr:`BaseJobResult.error` 供审计与重试排查：

    :attr:`JobStatus.FAILED` 通常意味着 Task 找不到 / 已被禁用 /
    任务缺少 ``conversation_id``（创建契约被破坏）/
    :class:`PlanBook` 写入失败。
    """


class _RunTaskJobRow(BaseJobRowMixin):
    __tablename__ = "run_task_jobs"
    __table_args__ = {"extend_existing": True}

    task_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: 是否用户/工具主动触发。``manual=True`` 跳过 since-recent
    #: 判定；``False`` 表示 cron / run_at 系统自触发。与
    #: :class:`~bus.firmwares.books.local.tasksBook.TaskRun.manual`
    #: 同构（两者都是 ``Boolean`` 列 + ``bool`` DTO，无 int 转换）。
    manual: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class runTaskJobBoard(BaseJobBoard[_RunTaskJobRow, RunTaskJob, RunTaskResult]):
    job_model = _RunTaskJobRow
    job_cls = RunTaskJob
    result_cls = RunTaskResult
