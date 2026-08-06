"""SettingsJob — 设置变更作业（同步写）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseJobQueue


@dataclass(frozen=True, slots=True)
class SettingsJob:
    key: str
    value: str


class _SettingRow(Base):
    __tablename__ = "settings"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class SettingsJobQueue(BaseJobQueue[None, SettingsJob, None]):

    def publish(self, job: SettingsJob) -> str:
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
