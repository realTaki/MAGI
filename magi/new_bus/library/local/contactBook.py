"""ContactBook + ContactNoteBook — unified person directory + per-fact notes.

Two tables:
- ``contacts``       — one row per person
- ``contact_notes``  — one row per fact (kind='permanent') or daily log (kind='daily')

Schema mirrors the old bus's ``contacts`` + ``contact_notes`` tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


SOURCE_MANUAL = "manual"
SOURCE_EVA = "eva"
SOURCE_SYSTEM = "system"

ROLE_ASSIGNED = "assigned"
ROLE_GUEST = "guest"
ALL_ROLES = frozenset({ROLE_ASSIGNED, ROLE_GUEST})


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contact:
    """Per-MAGI operator record.

    ``role`` is the MAGI-local role tag (``assigned`` /
    ``guest`` / ``contact``). Admin is **not** a column
    here — it's a MAGIS-level concept and lives in
    :class:`~magi.new_bus.library.magis.magisBook.MagisAdminBook`
    (``magis_admins`` table). A user can be ``assigned`` in
    this MAGI **and** admin in any MAGIS — the two flags
    are orthogonal. Tool gating combines both via
    :meth:`magi.tools.base.Tool.gate`.
    """

    id: int
    name: str
    display_name: str | None = None
    role: str = ROLE_GUEST
    telegram_id: int | None = None
    separated_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ContactNote:
    id: int
    contact_id: int
    note: str
    source: str = SOURCE_EVA
    kind: str = "permanent"
    note_date: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _ContactRow(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_GUEST)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    separated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class _ContactNoteRow(Base):
    __tablename__ = "contact_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_EVA
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="permanent"
    )
    note_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Books ---------------------------------------------------------------


class ContactBook(BaseBook[_ContactRow, Contact]):
    model_cls = _ContactRow
    dto_cls = Contact

    def get(self, *, contact_id: int) -> Contact | None:
        with self._factory.session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.id == contact_id))
            return self._row_to_dto(row) if row else None

    def get_by_telegram(self, *, telegram_id: int) -> Contact | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ContactRow).where(_ContactRow.telegram_id == telegram_id)
            )
            return self._row_to_dto(row) if row else None

    def add(self, *, name: str, role: str = ROLE_GUEST,
            display_name: str | None = None,
            telegram_id: int | None = None) -> Contact:
        with self._factory.session() as s:
            row = _ContactRow(
                name=name, role=role, display_name=display_name,
                telegram_id=telegram_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def list_all(self) -> list[Contact]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ContactRow).order_by(_ContactRow.id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_active(self) -> list[Contact]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ContactRow)
                .where(_ContactRow.separated_at.is_(None))
                .order_by(_ContactRow.id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def separate(self, *, contact_id: int) -> None:
        with self._factory.session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.id == contact_id))
            if row is None:
                return
            row.separated_at = utcnow_naive()
            s.commit()


class ContactNoteBook(BaseBook[_ContactNoteRow, ContactNote]):
    model_cls = _ContactNoteRow
    dto_cls = ContactNote

    def list_for_contact(self, *, contact_id: int) -> list[ContactNote]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ContactNoteRow)
                .where(_ContactNoteRow.contact_id == contact_id)
                .order_by(_ContactNoteRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, contact_id: int, note: str, source: str = SOURCE_EVA,
            kind: str = "permanent") -> ContactNote:
        with self._factory.session() as s:
            row = _ContactNoteRow(
                contact_id=contact_id, note=note, source=source, kind=kind,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


__all__ = [
    "Contact",
    "ContactNote",
    "ContactBook",
    "ContactNoteBook",
    "_ContactRow",
    "_ContactNoteRow",
    "ROLE_ASSIGNED",
    "ROLE_GUEST",
    "ALL_ROLES",
    "SOURCE_MANUAL",
    "SOURCE_EVA",
    "SOURCE_SYSTEM",
]
