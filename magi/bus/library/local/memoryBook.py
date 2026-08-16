"""MemoryBook — long-term self memory (one row per fact/ongoing).

Schema for the ``memory_entries`` table.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import enum_column, utcnow_naive
from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin


class MemoryKind(StrEnum):
    """Memory-kind discriminator stored on ``Memory.kind``.

    Two-way split by **purpose**, not lifecycle:

    * ``MemoryKind.FACT``       — durable knowledge about a
      contact (a fact the agent should recall across sessions).
      The ``remember`` tool writes these.
    * ``MemoryKind.QUICK_NOTE`` — transient todo / scratch /
      follow-up the agent parked for itself; can be promoted
      to a fact via ``update_memory`` (kind is otherwise
      immutable — delete + re-add).

    ``StrEnum`` rather than bare constants so typos are
    caught at lookup time instead of silently comparing
    False: every member is still a ``str``
    (``MemoryKind.FACT == "fact"``), so ORM columns,
    ``asdict`` serialisation and existing rows keep
    working unchanged. Mirrors
    :class:`magi.bus.library.local.contactBook.NoteKind`.
    """

    FACT = "fact"
    QUICK_NOTE = "quick_note"


# -- public dataclass ----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Memory(BaseRecord):
    contact_id: int  # 所属联系人 ID
    kind: MemoryKind  # 记忆类型（fact/quick_note）
    subject: str  # 简短标题
    body: str  # 完整内容
    priority: int = 3
    completed_at: datetime | None = None  # 完成时间（None=未完成）

# -- internal ORM --------------------------------------------------------


class _MemoryRow(BaseRecordMixin):
    __tablename__ = "memory_entries"
    # ``rememberNotify`` (in ``magi.bus.guild``) registers a Table
    # with the same name; whichever module is imported first wins and
    # the other must opt-in to sharing the existing Table object.
    # SQLAlchemy convention: dict kwargs must come last in the tuple.
    __table_args__ = (
        Index("ix_memory_entries_owner_priority", "contact_id", "completed_at", "priority"),
        {"extend_existing": True},
    )

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MemoryKind] = mapped_column(enum_column(MemoryKind), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# -- Book -----------------------------------------------------------------


class MemoryBook(BaseBook[_MemoryRow, Memory]):
    model_cls = _MemoryRow
    record_cls = Memory

    def list_by_owner(self, *, contact_id: int) -> list[Memory]:
        with self._session() as s:
            rows = s.scalars(
                select(_MemoryRow)
                .where(_MemoryRow.contact_id == contact_id)
                .order_by(_MemoryRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def complete(self, *, memory_id: int) -> Memory:
        """Mark an ongoing memory as done, idempotently.

        Raises :class:`LookupError` if the row doesn't
        exist. Re-calling on an already-completed row
        returns the existing DTO untouched so the LLM
        tool can serialise the same shape either way.
        """
        record = self.get(memory_id)
        if record is None:
            raise LookupError(f"memory row {memory_id} not found")
        if record.completed_at is None:
            self.update(record.with_changes(completed_at=utcnow_naive()))
        return self.get(memory_id)  # type: ignore[return-value]


__all__ = [
    "Memory",
    "MemoryBook",
    "MemoryKind",
    "_MemoryRow",
]
