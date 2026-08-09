"""HookSignoffBook — read-side view of pending async plugin acknowledgements.

This Book exposes the read-side view (which signoffs are pending for
which plugin on which subject), so a dispatcher (e.g. a worker) can
pick them up.

Schema for the ``hook_signoffs`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Integer,
    JSON,
    String,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook
from magi.bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class HookSignoff:
    id: int
    subject_type: str
    subject_id: str
    hook_point: str
    plugin_id: str
    status: str = "pending"
    payload: dict[str, Any] | None = None
    created_at: datetime | None = None
    dispatched_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _HookSignoffRow(Base):
    __tablename__ = "hook_signoffs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hook_point: Mapped[str] = mapped_column(String(64), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# -- Book ----------------------------------------------------------------


class HookSignoffBook(BaseBook[_HookSignoffRow, HookSignoff]):
    """Read-side Book for the ``hook_signoffs`` table.

    Callers can read pending signoffs here; they should not write
    or delete rows directly.
    """

    model_cls = _HookSignoffRow
    dto_cls = HookSignoff

    def get(self, *, signoff_id: int) -> HookSignoff | None:
        with self._session() as s:
            row = s.scalar(
                select(_HookSignoffRow).where(_HookSignoffRow.id == signoff_id)
            )
            return self._row_to_dto(row) if row else None

    def list_pending(self) -> list[HookSignoff]:
        with self._session() as s:
            rows = s.scalars(
                select(_HookSignoffRow)
                .where(_HookSignoffRow.status == "pending")
                .order_by(_HookSignoffRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_for_plugin(self, *, plugin_id: str) -> list[HookSignoff]:
        with self._session() as s:
            rows = s.scalars(
                select(_HookSignoffRow)
                .where(_HookSignoffRow.plugin_id == plugin_id)
                .order_by(_HookSignoffRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]


__all__ = ["HookSignoff", "HookSignoffBook", "_HookSignoffRow"]
