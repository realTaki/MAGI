"""ContactBook — 联系人簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Contact:
    contact_id: str
    name: str
    person_id: str | None = None
    notes: str | None = None


class _ContactRow(Base):
    __tablename__ = "contact_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    person_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class ContactBook(BaseBook[_ContactRow, Contact]):
    model_cls = _ContactRow
    dto_cls = Contact

    def get(self, *, contact_id: str) -> Contact | None:
        with self._session() as s:
            row = s.scalar(
                select(_ContactRow).where(_ContactRow.contact_id == contact_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Contact]:
        with self._session() as s:
            rows = s.scalars(
                select(_ContactRow).order_by(_ContactRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def search(self, *, query: str) -> list[Contact]:
        with self._session() as s:
            rows = s.scalars(
                select(_ContactRow)
                .where(_ContactRow.name.contains(query))
                .order_by(_ContactRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
