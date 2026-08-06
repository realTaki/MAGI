"""scheduleTaskJob — 任务变更作业（同步写）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseNotifyQueue


@dataclass(frozen=True, slots=True)
class ScheduleTaskJob:
    name: str
    schedule: str
    prompt: str | None = None
    enabled: bool = True
    task_id: str | None = None  # None=新建, 有值=更新


class _TaskRow(Base):
    __tablename__ = "tasks"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class scheduleTaskJob(BaseNotifyQueue[ScheduleTaskJob]):

    def publish(self, job: ScheduleTaskJob) -> str:
        with self._session() as s:
            if job.task_id:
                row = s.scalar(
                    select(_TaskRow).where(_TaskRow.task_id == job.task_id)
                )
                if row:
                    row.name = job.name
                    row.schedule = job.schedule
                    row.prompt = job.prompt
                    row.enabled = job.enabled
                    s.commit()
                    return job.task_id
            row = _TaskRow(
                task_id=job.task_id or uuid.uuid4().hex,
                name=job.name,
                schedule=job.schedule,
                prompt=job.prompt,
                enabled=job.enabled,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.task_id
