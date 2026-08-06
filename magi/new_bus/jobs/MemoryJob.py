"""MemoryJob — 记忆变更作业（同步写）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseJobQueue


@dataclass(frozen=True, slots=True)
class MemoryJob:
    owner_id: str
    kind: str
    content: str
    memory_id: str | None = None


class _MemoryRow(Base):
    __tablename__ = "memory_entries"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class MemoryJobQueue(BaseJobQueue[None, MemoryJob, None]):

    def publish(self, job: MemoryJob) -> str:
        with self._session() as s:
            if job.memory_id:
                row = s.scalar(
                    select(_MemoryRow).where(_MemoryRow.memory_id == job.memory_id)
                )
                if row:
                    row.kind = job.kind
                    row.content = job.content
                    s.commit()
                    return job.memory_id
            mid = job.memory_id or uuid.uuid4().hex
            s.add(_MemoryRow(
                memory_id=mid,
                owner_id=job.owner_id,
                kind=job.kind,
                content=job.content,
            ))
            s.flush()
            s.commit()
            return mid
