"""setSettingJob — 设置变更作业（同步写）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseNotifyQueue


@dataclass(frozen=True, slots=True)
class SetSettingJob:
    key: str
    value: str


class _SettingRow(Base):
    __tablename__ = "settings"
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class setSettingJob(BaseNotifyQueue[SetSettingJob]):

    def publish(self, job: SetSettingJob) -> str:
        with self._session() as s:
            existing = s.scalar(
                select(_SettingRow).where(_SettingRow.key == job.key)
            )
            if existing:
                existing.value = job.value
            else:
                s.add(_SettingRow(key=job.key, value=job.value))
            s.commit()
            return job.key
