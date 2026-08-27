"""ContactNoteBook — durable notes attached to local Contacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime


class NoteKind(StrEnum):
    """Whether a note is permanent knowledge or one daily log entry."""

    PERMANENT = "permanent"
    DAILY = "daily"


@dataclass(kw_only=True)
class ContactNote(BaseRecord):
    """One note belonging to a Contact."""

    contact_id: int
    note: str
    kind: NoteKind = NoteKind.PERMANENT
    note_date: BaseTime | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        note = super().parse(data)
        if not isinstance(note.kind, NoteKind):
            note.kind = NoteKind(note.kind)
        return note


class ContactNoteRow(BaseRecordMixin):
    __tablename__ = "books_contact_notes"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default=NoteKind.PERMANENT.value)
    note_date: Mapped[BaseTime | None] = mapped_column(DateTime, nullable=True)


class ContactNoteBook(BaseBook[ContactNote]):
    record_cls = ContactNote
    row_cls = ContactNoteRow
