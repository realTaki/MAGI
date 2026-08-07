"""MemoryBook — long-term self memory (one row per fact/ongoing).

Schema mirrors the old bus's ``memory_entries`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


KIND_IMPORTANT = "important"
KIND_ONGOING = "ongoing"
ALL_KINDS = frozenset({KIND_IMPORTANT, KIND_ONGOING})

SOURCE_MANUAL = "manual"
SOURCE_EVA = "eva"
SOURCE_SYSTEM = "system"


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    uid: int
    kind: str
    subject: str
    body: str
    importance: int = 3
    source: str = SOURCE_EVA
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _MemoryRow(Base):
    __tablename__ = "memory_entries"
    # ``rememberNotify`` (in ``magi.new_bus.guild``) registers a Table
    # with the same name; whichever module is imported first wins and
    # the other must opt-in to sharing the existing Table object.
    # SQLAlchemy convention: dict kwargs must come last in the tuple.
    __table_args__ = (
        Index("ix_memory_entries_owner_importance", "uid", "completed_at", "importance"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_EVA
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book -----------------------------------------------------------------


class MemoryBook(BaseBook[_MemoryRow, Memory]):
    model_cls = _MemoryRow
    dto_cls = Memory

    def get(self, *, memory_id: int) -> Memory | None:
        with self._session() as s:
            row = s.scalar(select(_MemoryRow).where(_MemoryRow.id == memory_id))
            return self._row_to_dto(row) if row else None

    def list_by_owner(self, *, uid: int) -> list[Memory]:
        with self._session() as s:
            rows = s.scalars(
                select(_MemoryRow)
                .where(_MemoryRow.uid == uid)
                .order_by(_MemoryRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, uid: int, kind: str, subject: str, body: str,
            importance: int = 3, source: str = SOURCE_EVA) -> Memory:
        with self._session() as s:
            row = _MemoryRow(
                uid=uid, kind=kind, subject=subject,
                body=body, importance=importance, source=source,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def mark_completed(self, *, memory_id: int) -> None:
        with self._session() as s:
            row = s.scalar(select(_MemoryRow).where(_MemoryRow.id == memory_id))
            if row is None:
                return
            row.completed_at = utcnow_naive()
            s.commit()

    def delete(self, *, memory_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(select(_MemoryRow).where(_MemoryRow.id == memory_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["Memory", "MemoryBook", "_MemoryRow", "ALL_KINDS", "KIND_IMPORTANT", "KIND_ONGOING"]
