"""ActionItemBook — 待办事项簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class ActionItem:
    item_id: str
    title: str
    description: str | None = None
    done: bool = False


class _ActionItemRow(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class ActionItemBook(BaseBook[_ActionItemRow, ActionItem]):
    model_cls = _ActionItemRow
    dto_cls = ActionItem

    def get(self, *, item_id: str) -> ActionItem | None:
        with self._session() as s:
            row = s.scalar(
                select(_ActionItemRow).where(_ActionItemRow.item_id == item_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self, *, include_done: bool = False) -> list[ActionItem]:
        with self._session() as s:
            q = select(_ActionItemRow).order_by(_ActionItemRow.created_at.desc())
            if not include_done:
                q = q.where(_ActionItemRow.done == False)
            rows = s.scalars(q).all()
            return [self._row_to_dto(r) for r in rows]
