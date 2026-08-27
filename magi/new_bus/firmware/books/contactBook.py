"""ContactBook — local people known by one Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

from sqlalchemy import BigInteger, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime, utcnow


class ContactRole(StrEnum):
    """One Runtime's relationship to a human or agent contact."""

    ASSIGNED = "assigned"
    GUEST = "guest"
    MAGI = "magi"
    THIRD_PARTY_AGENT = "third_party_agent"


@dataclass(kw_only=True)
class Contact(BaseRecord):
    """A human or agent contact local to this Runtime."""

    name: str
    display_name: str | None = None
    role: ContactRole = ContactRole.GUEST
    tgid: int | None = None
    last_seen_at: BaseTime = field(default_factory=utcnow)

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        contact = super().parse(data)
        if not isinstance(contact.role, ContactRole):
            contact.role = ContactRole(contact.role)
        return contact


class ContactRow(BaseRecordMixin):
    __tablename__ = "books_contacts"

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default=ContactRole.GUEST.value)
    tgid: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    last_seen_at: Mapped[BaseTime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ContactBook(BaseBook[Contact]):
    record_cls = Contact
    row_cls = ContactRow
