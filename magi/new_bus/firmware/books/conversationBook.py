"""ConversationBook — current conversations.

The record type :class:`Conversation` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook
from ...base.BaseRecord import BaseRecord
from ...base.BaseRecordMixin import BaseRecordMixin


@dataclass(kw_only=True)
class Conversation(BaseRecord):
    """One row in ConversationBook.

    delivery_address: where replies go
    contact_id: owning contact
    channel: inbound channel name
    title: optional display name
    summary: compacted summary
    last_compaction_at: when summary was last written
    """

    delivery_address: str
    contact_id: int
    channel: str
    title: str = ""
    summary: str = ""
    last_compaction_at: datetime | None = None

    BOOK: ClassVar[str] = "conversations"


class ConversationRow(BaseRecordMixin):
    __tablename__ = "books_conversations"

    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_compaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConversationBook(BaseBook):
    name = Conversation.BOOK
    record_cls = Conversation
