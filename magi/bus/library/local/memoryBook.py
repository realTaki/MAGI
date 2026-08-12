"""MemoryBook — long-term self memory (one row per fact/ongoing).

Schema for the ``memory_entries`` table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook


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


# Column-length invariants. Mirror the ORM column
# declarations (``String(200)`` / ``Text``) and the
# 8 KiB body cap the old service enforced. The Book
# owns the writes so every caller (LLM-driven tool,
# dashboard API, future agent loop) gets the same
# validation without each re-implementing length checks.
_SUBJECT_MAX = 200
_BODY_MAX = 8 * 1024


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Memory:
    id: int  # 主键（自增）
    contact_id: int  # 所属联系人 ID
    kind: MemoryKind  # 记忆类型（fact/quick_note）
    subject: str  # 简短标题
    body: str  # 完整内容
    priority: int = 3  # 优先级（1..5，越大越重要）
    completed_at: datetime | None = None  # 完成时间（None=未完成）
    created_at: datetime | None = None  # 创建时间
    updated_at: datetime | None = None  # 最近更新时间

    def to_dict(self) -> dict:
        """Wire-shape for JSON serialisation.

        ``BaseBook._row_to_dto`` already renders every
        ``datetime`` column through :func:`to_iso`, so
        the timestamp fields are ISO-8601 ``Z`` strings
        by the time this runs.
        """
        return asdict(self)


# -- internal ORM --------------------------------------------------------


class _MemoryRow(Base):
    __tablename__ = "memory_entries"
    # ``rememberNotify`` (in ``magi.bus.guild``) registers a Table
    # with the same name; whichever module is imported first wins and
    # the other must opt-in to sharing the existing Table object.
    # SQLAlchemy convention: dict kwargs must come last in the tuple.
    __table_args__ = (
        Index("ix_memory_entries_owner_priority", "contact_id", "completed_at", "priority"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book -----------------------------------------------------------------


class MemoryBook(BaseBook[_MemoryRow, Memory]):
    model_cls = _MemoryRow
    dto_cls = Memory

    @staticmethod
    def _validate_subject(subject: str) -> str:
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("subject must be a non-empty string")
        if len(subject) > _SUBJECT_MAX:
            raise ValueError(f"subject length {len(subject)} exceeds maximum {_SUBJECT_MAX}")
        return subject.strip()

    @staticmethod
    def _validate_body(body: str) -> str:
        if not isinstance(body, str) or not body.strip():
            raise ValueError("body must be a non-empty string")
        if len(body) > _BODY_MAX:
            raise ValueError(f"body length {len(body)} exceeds maximum {_BODY_MAX}")
        return body.strip()

    @staticmethod
    def _validate_priority(priority: int) -> None:
        if not isinstance(priority, int) or not 1 <= priority <= 5:
            raise ValueError("priority must be 1..5")

    def get(self, *, memory_id: int) -> Memory | None:
        with self._session() as s:
            row = s.scalar(select(_MemoryRow).where(_MemoryRow.id == memory_id))
            return self._row_to_dto(row) if row else None

    def list_by_owner(self, *, contact_id: int) -> list[Memory]:
        with self._session() as s:
            rows = s.scalars(
                select(_MemoryRow)
                .where(_MemoryRow.contact_id == contact_id)
                .order_by(_MemoryRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(
        self,
        *,
        contact_id: int,
        kind: MemoryKind,
        subject: str,
        body: str,
        priority: int = 3,
    ) -> Memory:
        """Insert a memory row after enforcing write invariants.

        Raises :class:`ValueError` on invariant violation
        (subject / body non-empty, length caps, ``kind``
        in :class:`MemoryKind`, ``priority`` 1..5).
        The tool worker / dashboard API catch and surface
        as ``is_error=True`` / 4xx.
        """
        subject = self._validate_subject(subject)
        body = self._validate_body(body)
        if kind not in MemoryKind:
            raise ValueError(
                f"kind must be one of "
                f"{sorted(k.value for k in MemoryKind)!r}, got {kind!r}"
            )
        self._validate_priority(priority)

        with self._session() as s:
            row = _MemoryRow(
                contact_id=contact_id,
                kind=kind,
                subject=subject,
                body=body,
                priority=priority,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

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
        if subject is not None:
            subject = self._validate_subject(subject)
        if body is not None:
            body = self._validate_body(body)
        if priority is not None:
            self._validate_priority(priority)

        with self._session() as s:
            row = s.get(_MemoryRow, memory_id)
            if row is None:
                raise LookupError(f"memory row {memory_id} not found")
            if subject is not None:
                row.subject = subject
            if body is not None:
                row.body = body
            if priority is not None:
                row.priority = priority
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
