"""MemoryJob — writes to the ``memory_entries`` table.

Sync writes only.  The corresponding ``MemoryBook`` provides reads.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive
from magi.new_bus.books.local.memory import (
    SOURCE_EVA,
    KIND_IMPORTANT,
    KIND_ONGOING,
)

logger = logging.getLogger("magi.new_bus.jobs.memory")


class _JMemoryRow(JobBase):
    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(nullable=False)
    subject: Mapped[str] = mapped_column(nullable=False)
    body: Mapped[str] = mapped_column(nullable=False)
    importance: Mapped[int] = mapped_column(default=3, nullable=False)
    source: Mapped[str] = mapped_column(default=SOURCE_EVA, nullable=False)
    completed_at = mapped_column(nullable=True)
    created_at = mapped_column(default=job_utcnow_naive, nullable=False)
    updated_at = mapped_column(
        default=job_utcnow_naive, onupdate=job_utcnow_naive, nullable=False,
    )


class MemoryJob(BaseJob):
    """Write side of the memory domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(
        self,
        *,
        uid: int,
        kind: str,
        subject: str,
        body: str,
        importance: int = 3,
        source: str = SOURCE_EVA,
    ) -> int:
        with self._factory.session() as s:
            row = _JMemoryRow(
                uid=uid, kind=kind, subject=subject,
                body=body, importance=importance, source=source,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def mark_completed(self, *, memory_id: int) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JMemoryRow).where(_JMemoryRow.id == memory_id)
            )
            if row is None:
                return
            row.completed_at = job_utcnow_naive()
            s.commit()

    def delete(self, *, memory_id: int) -> bool:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JMemoryRow).where(_JMemoryRow.id == memory_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["MemoryJob", "KIND_IMPORTANT", "KIND_ONGOING"]
