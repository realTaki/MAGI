"""MemoryBook — long-term self memory (one row per fact/ongoing).

Schema for the ``memory_entries`` table.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, Strict, StringConstraints
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import enum_column, utcnow_naive
from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin, record


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


@record
class Memory(BaseRecord):
    contact_id: Annotated[int, Strict()]  # 所属联系人 ID
    kind: MemoryKind  # 记忆类型（fact/quick_note）
    subject: Annotated[
        str, Strict(), StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]  # 简短标题
    body: Annotated[
        str, Strict(), StringConstraints(strip_whitespace=True, min_length=1, max_length=8 * 1024)
    ]  # 完整内容
    priority: Annotated[int, Strict(), Field(ge=1, le=5)] = 3
    completed_at: Annotated[datetime, Strict()] | None = None  # 完成时间（None=未完成）

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
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
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
        with self._session() as s:
            row = s.get(_MemoryRow, memory_id)
            if row is None:
                raise LookupError(f"memory row {memory_id} not found")
            if row.completed_at is None:
                row.completed_at = utcnow_naive()
                s.commit()
                s.refresh(row)
            return self._row_to_dto(row)

    def update(
        self,
        *,
        memory_id: int,
        subject: str | None = None,
        body: str | None = None,
        priority: int | None = None,
    ) -> Memory:
        """Patch mutable fields after enforcing their invariants.

        Only ``subject``, ``body`` and ``priority`` are
        mutable — ``kind`` and ``contact_id`` are intentionally
        frozen (delete + re-add if you need to change
        those). Raises :class:`LookupError` if the row
        is missing, :class:`ValueError` on any supplied
        field that violates the validator.
        """
        with self._session() as s:
            row = s.get(_MemoryRow, memory_id)
            if row is None:
                raise LookupError(f"memory row {memory_id} not found")
            candidate = Memory(
                contact_id=row.contact_id,
                kind=row.kind,
                subject=subject if subject is not None else row.subject,
                body=body if body is not None else row.body,
                priority=priority if priority is not None else row.priority,
                completed_at=row.completed_at,
            )
            if subject is not None:
                row.subject = candidate.subject
            if body is not None:
                row.body = candidate.body
            if priority is not None:
                row.priority = candidate.priority
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def delete(self, *, memory_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(select(_MemoryRow).where(_MemoryRow.id == memory_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = [
    "Memory",
    "MemoryBook",
    "MemoryKind",
    "_MemoryRow",
]
