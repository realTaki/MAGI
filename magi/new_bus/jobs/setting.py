"""SettingJob — writes to the ``settings`` table (KV)."""

from __future__ import annotations

import logging

from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive

logger = logging.getLogger("magi.new_bus.jobs.setting")


class _JSettingRow(JobBase):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at = mapped_column(
        default=job_utcnow_naive, onupdate=job_utcnow_naive, nullable=False,
    )


class SettingJob(BaseJob):
    """Write side of the settings KV."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def set(self, *, key: str, value: str) -> str:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JSettingRow).where(_JSettingRow.key == key)
            )
            if row is None:
                row = _JSettingRow(key=key, value=value)
                s.add(row)
            else:
                row.value = value
            s.commit()
            s.refresh(row)
        return row.key

    def delete(self, *, key: str) -> bool:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JSettingRow).where(_JSettingRow.key == key)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["SettingJob"]
