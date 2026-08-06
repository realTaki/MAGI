"""ActionItemJob — writes to the ``action_items`` table."""

from __future__ import annotations

import logging

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive

logger = logging.getLogger("magi.new_bus.jobs.action_item")


class _JActionItemRow(JobBase):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(default="open", nullable=False)
    severity: Mapped[str] = mapped_column(default="info", nullable=False)
    created_at = mapped_column(DateTime, default=job_utcnow_naive, nullable=False)
    resolved_at = mapped_column(DateTime, nullable=True)


class ActionItemJob(BaseJob):
    """Write side of the action-item domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(
        self,
        *,
        uid: int,
        kind: str,
        title: str,
        body: str,
        severity: str = "info",
    ) -> int:
        with self._factory.session() as s:
            row = _JActionItemRow(
                uid=uid, kind=kind, title=title, body=body, severity=severity,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def mark_done(self, *, item_id: int) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JActionItemRow).where(_JActionItemRow.id == item_id)
            )
            if row is None:
                return
            row.status = "done"
            row.resolved_at = job_utcnow_naive()
            s.commit()


__all__ = ["ActionItemJob"]
