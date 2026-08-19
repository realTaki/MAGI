"""MessageBook — current messages.

The record type :class:`Message` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook
from ...base.BaseRecord import BaseRecord, BaseRecordMixin
from ...base.time import utcnow


@dataclass(kw_only=True)
class Message(BaseRecord):
    """One row in MessageBook.

    role: ``user`` | ``assistant`` | ``system`` | ``tool``
    content: non-empty text
    conversation_id: optional Conversation.id
    timestamp: when the message was produced
    archived: hidden from the live transcript
    """

    role: str
    content: str
    conversation_id: int | None = None
    timestamp: datetime = field(default_factory=utcnow)
    archived: bool = False

    BOOK: ClassVar[str] = "messages"


class MessageRow(BaseRecordMixin):
    __tablename__ = "books_messages"

    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("books_conversations.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MessageBook(BaseBook):
    name = Message.BOOK
    record_cls = Message
    row_cls = MessageRow
