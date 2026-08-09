"""MemoryBook — long-term self memory (one row per fact/ongoing).

Schema for the ``memory_entries`` table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook
from magi.bus.db.base import Base, utcnow_naive


KIND_FACT = "fact"
KIND_QUICK_NOTE = "quick_note"
ALL_KINDS = frozenset({KIND_FACT, KIND_QUICK_NOTE})

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
    id: int
    uid: int
    kind: str
    subject: str
    body: str
    priority: int = 3
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

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
        Index("ix_memory_entries_owner_priority", "uid", "completed_at", "priority"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
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

    @staticmethod
    def _validate_subject(subject: str) -> str:
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("subject must be a non-empty string")
        if len(subject) > _SUBJECT_MAX:
            raise ValueError(
                f"subject length {len(subject)} exceeds maximum {_SUBJECT_MAX}"
            )
        return subject.strip()

    @staticmethod
    def _validate_body(body: str) -> str:
        if not isinstance(body, str) or not body.strip():
            raise ValueError("body must be a non-empty string")
        if len(body) > _BODY_MAX:
            raise ValueError(
                f"body length {len(body)} exceeds maximum {_BODY_MAX}"
            )
        return body.strip()

    @staticmethod
    def _validate_priority(priority: int) -> None:
        if not isinstance(priority, int) or not 1 <= priority <= 5:
            raise ValueError("priority must be 1..5")

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

    def add(
        self,
        *,
        uid: int,
        kind: str,
        subject: str,
        body: str,
        priority: int = 3,
    ) -> Memory:
        """Insert a memory row after enforcing write invariants.

        Raises :class:`ValueError` on invariant violation
        (subject / body non-empty, length caps, ``kind``
        in :data:`ALL_KINDS`, ``priority`` 1..5). The
        tool worker / dashboard API catch and surface as
        ``is_error=True`` / 4xx.
        """
        subject = self._validate_subject(subject)
        body = self._validate_body(body)
        if kind not in ALL_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(ALL_KINDS)!r}, got {kind!r}"
            )
        self._validate_priority(priority)

        with self._session() as s:
            row = _MemoryRow(
                uid=uid,
                kind=kind,
                subject=subject,
                body=body,
                priority=priority,
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
        mutable — ``kind`` and ``uid`` are intentionally
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
    "Memory", "MemoryBook", "_MemoryRow",
    "ALL_KINDS", "KIND_FACT", "KIND_QUICK_NOTE",
]