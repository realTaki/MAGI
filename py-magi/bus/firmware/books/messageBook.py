"""MessageBook — current messages.

The record type :class:`Message` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime, utcnow


@dataclass(kw_only=True)
class Message(BaseRecord):
    """One row in MessageBook.

    contact_id: speaker Contact.id
    content: non-empty text
    conversation_id: optional Conversation.id
    timestamp: when the message was produced
    archived: hidden from the live transcript
    """

    contact_id: int
    content: str
    conversation_id: int
    timestamp: BaseTime = field(default_factory=utcnow)
    archived: bool = False


class MessageRow(BaseRecordMixin):
    __tablename__ = "books_messages"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("books_conversations.id"), nullable=False
    )
    timestamp: Mapped[BaseTime] = mapped_column(DateTime, default=utcnow, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MessageBook(BaseBook[Message]):
    record_cls = Message
    row_cls = MessageRow
