"""setConfigJob — 配置变更作业（同步写）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseNotifyQueue


@dataclass(frozen=True, slots=True)
class SetConfigJob:
    config_key: str
    config_value: dict | None = None


class _ConfigRow(Base):
    __tablename__ = "config_entries"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    config_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class setConfigJob(BaseNotifyQueue[SetConfigJob]):

    def publish(self, job: SetConfigJob) -> str:
        with self._session() as s:
            existing = s.scalar(
                select(_ConfigRow).where(_ConfigRow.config_key == job.config_key)
            )
            if existing:
                existing.config_value = job.config_value
            else:
                existing = _ConfigRow(
                    config_key=job.config_key,
                    config_value=job.config_value,
                )
                s.add(existing)
            s.flush()
            s.commit()
            return job.config_key
