"""DeliveryJob — 出站投递作业。

agent 产出回复 → 入队 → worker 投递到渠道
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseJobQueue


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    channel: str
    payload: dict
    destination: str | None = None
    run_id: str = ""
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryJobResult:
    job_id: str
    success: bool
    error: str | None = None


class _DeliveryJobRow(Base):
    __tablename__ = "delivery_outbox"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    run_id: Mapped[str] = mapped_column(String(64), default="")
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


class DeliveryJobQueue(BaseJobQueue[_DeliveryJobRow, DeliveryJob, DeliveryJobResult]):
    job_model = _DeliveryJobRow
    job_cls = DeliveryJob
    result_cls = DeliveryJobResult

    def publish(self, job: DeliveryJob) -> str:
        with self._factory.session() as s:
            row = _DeliveryJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                channel=job.channel,
                payload=job.payload,
                destination=job.destination,
                run_id=job.run_id,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
