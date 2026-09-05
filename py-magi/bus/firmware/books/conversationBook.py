"""ConversationBook — current conversations.

The record type :class:`Conversation` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    DateTime,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime


@dataclass(kw_only=True)
class Conversation(BaseRecord):
    """One row in ConversationBook.

    ``channel`` and ``delivery_address`` identify the transport endpoint
    for this conversation; no channel-specific identity is stored on Contact.

    delivery_address: where replies go on this channel
    channel: transport/channel name
    topic: optional conversation topic
    instruction: optional instruction for this conversation
    info: optional free-text notes
    summary: compacted summary
    last_compaction_at: when summary was last written
    """

    delivery_address: str | None = None
    channel: str | None = None
    topic: str | None = None
    instruction: str | None = None
    info: str | None = None
    summary: str = ""
    last_compaction_at: BaseTime | None = None


class ConversationRow(BaseRecordMixin):
    __tablename__ = "books_conversations"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "delivery_address",
            name="uq_books_conversations_endpoint",
        ),
    )

    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    info: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_compaction_at: Mapped[BaseTime | None] = mapped_column(DateTime, nullable=True)


class ConversationBook(BaseBook[Conversation]):
    record_cls = Conversation
    row_cls = ConversationRow

    def add_for_channel(self, *, channel: str, delivery_address: str) -> int:
        """Return the conversation id for this channel + delivery_address, creating it if needed."""
        existing = self.list(channel=channel, delivery_address=delivery_address)
        if existing:
            return existing[0].id
        return self.add(
            Conversation(channel=channel, delivery_address=delivery_address)
        )
