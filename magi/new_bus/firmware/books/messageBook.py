"""MessageBook — current messages.

The record type :class:`Message` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime, utcnow


class MessageRole(StrEnum):
    """Roles accepted by the Firmware chat transcript."""
    # TODO-- 不需要这么多角色
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(kw_only=True)
class Message(BaseRecord):
    """One row in MessageBook.

    role: ``user`` | ``assistant`` | ``system`` | ``tool``
    content: non-empty text
    conversation_id: optional Conversation.id
    timestamp: when the message was produced
    archived: hidden from the live transcript
    """

    role: MessageRole
    content: str
    conversation_id: int | None = None
    timestamp: BaseTime = field(default_factory=utcnow)
    archived: bool = False


class MessageRow(BaseRecordMixin):
    __tablename__ = "books_messages"

    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("books_conversations.id"), nullable=True
    )
    timestamp: Mapped[BaseTime] = mapped_column(DateTime, default=utcnow, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MessageBook(BaseBook[Message]):
    record_cls = Message
    row_cls = MessageRow
