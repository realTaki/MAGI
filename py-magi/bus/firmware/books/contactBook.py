"""ContactBook — local people known by one Runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime, utcnow


class ContactRole(StrEnum):
    """One Runtime's relationship to a human or agent contact."""

    SYSTEM = "system"
    AUTHORIZED = "authorized"
    STRANGER = "stranger"
    MAGI = "magi"
    THIRD_PARTY_AGENT = "third_party_agent"


@dataclass(kw_only=True)
class Contact(BaseRecord):
    """A channel-independent human or agent known to this Runtime."""

    name: str
    nickname: str | None = None
    role: ContactRole = ContactRole.STRANGER
    last_seen_at: BaseTime = field(default_factory=utcnow)


class ContactRow(BaseRecordMixin):
    __tablename__ = "books_contacts"

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default=ContactRole.STRANGER.value)
    last_seen_at: Mapped[BaseTime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ContactBook(BaseBook[Contact]):
    record_cls = Contact
    row_cls = ContactRow

    def __init__(self, factory) -> None:
        super().__init__(factory)
        with self._session() as session:
            if session.get(ContactRow, 0) is None:
                session.add(
                    ContactRow(
                        id=0,
                        name="system",
                        role=ContactRole.SYSTEM.value,
                        last_seen_at=utcnow(),
                    )
                )
                session.commit()
