"""ContactNoteBook — durable notes attached to local Contacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


class NoteKind(StrEnum):
    """Whether a note is permanent knowledge or one daily log entry."""

    PERMANENT = "permanent"
    DAILY = "daily"


@dataclass(kw_only=True)
class ContactNote(BaseRecord):
    """One note belonging to a Contact."""

    contact_id: int
    note: str | None = "Nothing to say"
    kind: NoteKind | None = NoteKind.PERMANENT


class ContactNoteRow(BaseRecordMixin):
    __tablename__ = "books_contact_notes"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default=NoteKind.PERMANENT.value)


class ContactNoteBook(BaseBook[ContactNote]):
    record_cls = ContactNote
    row_cls = ContactNoteRow
