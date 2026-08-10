"""runTaskJobBoard — 任务触发作业板。

inter-worker / tool 统一触发接口：任何调用方
``bus.run_task_job_board.publish(RunTaskJob(task_id=...))``，
TaskWorker claim 后执行同一 ``_fire_task`` 路径。

触发来源 closed set:
  cron_tick | run_at_consume | api_manual_run | schedule_task_tool
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
    task_id: str
    manual: bool = True
    fired_by: str = "manual"
    conversation_id: str | None = None
    contact_id: int | None = None
    job_id: str = ""
    # Populated by ``BaseJobBoard._map_row`` on claim — not stored on
    # the row (the column exists as a counter only). Exposed here so
    # callers can observe lease-recovery behaviour (see
    # ``test_lease_expiry_reclaims_abandoned_job``).
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class RunTaskResult:
    job_id: str
    success: bool
    error: str | None = None


class _RunTaskJobRow(Base):
    __tablename__ = "run_task_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    manual: Mapped[int] = mapped_column(Integer, default=1)
    fired_by: Mapped[str] = mapped_column(String(32), default="manual")
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(
        type_=__import__("sqlalchemy").JSON, nullable=True,
    )
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive,
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
                fired_by=job.fired_by,
                conversation_id=job.conversation_id,
                contact_id=job.contact_id,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
