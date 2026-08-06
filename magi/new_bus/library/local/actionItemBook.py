"""ActionItemBook — dashboard to-do inbox.

Schema mirrors the old bus's ``action_items`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActionItem:
    id: int
    uid: int
    kind: str
    title: str
    body: str
    status: str = "open"
    severity: str = "info"
    created_at: datetime | None = None
    resolved_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _ActionItemRow(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="info"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# -- Book ----------------------------------------------------------------


class ActionItemBook(BaseBook[_ActionItemRow, ActionItem]):
    model_cls = _ActionItemRow
    dto_cls = ActionItem

    def get(self, *, item_id: int) -> ActionItem | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ActionItemRow).where(_ActionItemRow.id == item_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_owner(self, *, uid: int,
                       status: str | None = "open") -> list[ActionItem]:
        with self._factory.session() as s:
            stmt = select(_ActionItemRow).where(_ActionItemRow.uid == uid)
            if status is not None:
                stmt = stmt.where(_ActionItemRow.status == status)
            stmt = stmt.order_by(_ActionItemRow.created_at.desc())
            rows = s.scalars(stmt).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, uid: int, kind: str, title: str, body: str,
            severity: str = "info") -> ActionItem:
        with self._factory.session() as s:
            row = _ActionItemRow(
                uid=uid, kind=kind, title=title, body=body, severity=severity,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def mark_done(self, *, item_id: int) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ActionItemRow).where(_ActionItemRow.id == item_id)
            )
            if row is None:
                return
            row.status = "done"
            row.resolved_at = utcnow_naive()
            s.commit()


__all__ = ["ActionItem", "ActionItemBook", "_ActionItemRow"]
