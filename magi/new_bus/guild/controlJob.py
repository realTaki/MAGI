"""controlJobBoard — 控制信号作业。

系统级事件：provider 变更、runtime 重启等。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard


@dataclass(frozen=True, slots=True)
class ControlJob:
    kind: str
    payload: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ControlJobResult:
    job_id: str
    success: bool
    error: str | None = None


class _ControlJobRow(Base):
    __tablename__ = "control_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class controlJobBoard(BaseJobBoard[_ControlJobRow, ControlJob, ControlJobResult]):
    job_model = _ControlJobRow
    job_cls = ControlJob
    result_cls = ControlJobResult

    def publish(self, job: ControlJob) -> str:
        with self._factory.session() as s:
            row = _ControlJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                kind=job.kind,
                payload=job.payload,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
